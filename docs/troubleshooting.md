# Troubleshooting

- **Directory unavailable:** mount the drive/share under the same Windows account and rescan. A network disconnect records the affected file error without altering sources.
- **RAW preview unavailable:** install the `raw` optional dependency. LibRaw support varies by camera; paired JPEGs remain preferred.
- **AI model unavailable:** classical analysis is fully functional. Verify manifest path, weight checksum and ONNX provider.
- **Export is partial:** inspect the manifest and choose an empty destination outside every source tree. Rerunning never overwrites an existing file.
- **Diagnostics:** open `/api/diagnostics`. It reports versions and task states but does not send telemetry.
- **Database recovery:** stop FRAME and back up `.frame-data/frame.db`. Never edit the database while FRAME is running.
