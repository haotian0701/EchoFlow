# Mock demonstration audio

`mock-chime.mp3` is a locally synthesized two-second, 660 Hz sine tone with a fade-out.
It contains no recorded speech, music or external sound samples. It is test audio,
not narration of the approved script.

It was produced with FFmpeg 6.1:

```sh
ffmpeg -f lavfi -i 'sine=frequency=660:duration=2:sample_rate=44100' \
  -af 'afade=t=out:st=1.3:d=0.7' -codec:a libmp3lame -b:a 128k mock-chime.mp3
```

The application ships the resulting MP3; FFmpeg is not a runtime dependency.
