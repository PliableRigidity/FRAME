from pathlib import Path
from threading import Event
from PIL import Image
from sqlalchemy import select
from frame.ingestion import discover, group_assets, read_metadata, scan, stable_id, validate_source
from frame.models import Photo


def image(path: Path, color="red", size=(40, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def test_recursive_discovery_pairing_and_duplicate_names(tmp_path):
    image(tmp_path / "a" / "IMG_1.JPG")
    (tmp_path / "a" / "IMG_1.CR2").write_bytes(b"raw")
    image(tmp_path / "b" / "IMG_1.JPG")
    groups = group_assets(discover(tmp_path))
    assert sorted(map(len, groups)) == [1, 2]
    assert stable_id(tmp_path / "a" / "IMG_1.JPG") != stable_id(tmp_path / "b" / "IMG_1.JPG")


def test_missing_partner_and_corrupt_image(tmp_path):
    (tmp_path / "only.NEF").write_bytes(b"raw")
    (tmp_path / "bad.jpg").write_bytes(b"not-an-image")
    assert len(list(discover(tmp_path))) == 2
    assert read_metadata(tmp_path / "bad.jpg").error.startswith("Unreadable image")


def test_scan_persists_and_is_idempotent(app, tmp_path):
    source = tmp_path / "photos"
    image(source / "one.jpg", size=(80, 60))
    assert scan(source, app.state.factory, app.state.settings.thumbnail_dir, Event(), lambda *_: None) == (1, 1)
    scan(source, app.state.factory, app.state.settings.thumbnail_dir, Event(), lambda *_: None)
    with app.state.factory() as session:
        rows = session.scalars(select(Photo)).all()
        assert len(rows) == 1
        assert (rows[0].width, rows[0].height) == (80, 60)
        assert Path(rows[0].thumbnail_path).exists()


def test_cancelled_scan_stops_before_work(app, tmp_path):
    image(tmp_path / "one.jpg")
    cancelled = Event(); cancelled.set()
    assert scan(tmp_path, app.state.factory, app.state.settings.thumbnail_dir, cancelled, lambda *_: None)[0] == 0


def test_source_validation(tmp_path):
    assert validate_source(str(tmp_path)) == tmp_path.resolve()
    for value in ["", str(tmp_path / "missing")]:
        try:
            validate_source(value)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid source accepted")
