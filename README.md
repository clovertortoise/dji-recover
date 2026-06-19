# dji-recover
A Python utility to reconstruct truncated DJI MP4 recordings. It recovers raw video payloads from improperly finalized files by dynamically stripping interleaved proprietary telemetry data and repairing the underlying HEVC/H.264 Annex B stream.

## The Problem

When a DJI drone or action camera (e.g., Mavic, O3/O4 Air Units) loses power mid-recording, the MP4 container fails to write the `moov` atom (the master index). Standard video recovery tools (like `untrunc` or `recover_mp4`) routinely fail to repair these files because DJI interleaves proprietary telemetry tracks (`djmd` and `dbgi`) directly into the raw video payload.

When standard parsers encounter this telemetry data, they interpret it as severe NAL unit corruption, break the frame reference chain, and abort. 

## The Solution

This tool bypasses container-level parsers and interacts directly with the binary stream. 

**Core Capabilities:**
* **Native Execution:** No compiled C dependencies. Runs entirely in Python.
* **Dynamic Telemetry Masking:** Identifies and physically seeks over `djmd`/`dbgi` telemetry blocks using known bitwise masking offsets.
* **Sliding Window Resynchronization:** Bypasses undocumented `mdat` padding and recovers from mid-stream corruption by scanning for valid HEVC/H.264 frame signatures (`0x2801`, `0x0201`).
* **Header Grafting:** Bypasses hardcoded hardware profiles by surgically extracting and grafting the exact Video, Sequence, and Picture Parameter Sets (VPS/SPS/PPS) from a user-provided reference file.
* **Timing Reconstruction:** Forces FFmpeg to generate correct Presentation Time Stamps (PTS) and Decode Time Stamps (DTS), eliminating B-frame stuttering and datamoshing.

---

## Prerequisites

1. **Python 3.7+**
2. **FFmpeg:** Must be installed and accessible via your system's PATH.

## Usage

You must provide the corrupted video file and a "working reference" video file. The reference file must be recorded on the exact same camera using the exact same resolution, framerate, and codec settings.

**Syntax:**
`python native_dji_recover.py <corrupted_file.mp4> <reference_file.mp4> [framerate]`

**Example (Default 100fps):**
```bash
python native_dji_recover.py "bad.MP4" "good.MP4"

```

**Example (Custom Framerate):**

```bash
python native_dji_recover.py "bad.MP4" "good.MP4" 60

```

---

## Technical Workflow

1. **Header Extraction:** Invokes FFmpeg to extract the first frame of the working reference file, converting the containerized `hvcC` atom into a raw Annex B byte-stream (`valid_headers.hevc`).
2. **Payload Sanitization:** Opens the corrupted MP4, skips the `ftyp` and `moov` atoms, and enters the `mdat` payload. It reads NAL unit length prefixes. If a length matches a known telemetry block size, the parser steps over it. If a length is mathematically impossible (indicating severe corruption or undocumented padding), it engages a 64KB sliding window to hunt for the next valid frame signature. Valid frames are written to a temporary raw `.hevc` file.
3. **Muxing and Alignment:** FFmpeg is invoked using `-fflags +igndts+genpts` to read the concatenated raw bitstream, calculate the correct B-frame presentation order, and wrap the data into a playable, standardized MP4 container.

## Acknowledgments

The bitwise masking logic used to identify specific DJI telemetry block sizes is ported and adapted from the open-source C project [djifix](https://djifix.live555.com/) by Live Networks, Inc.
