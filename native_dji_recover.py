"""
Native DJI Video Recovery

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

Telemetry masking logic adapted from 'djifix' by Live Networks, Inc.
Copyright (c) 2014-2026 Live Networks, Inc. All rights reserved.
"""

import subprocess
import struct
import sys
import os
from pathlib import Path

class NativeDJIRecover:
    def __init__(self, corrupted_path, reference_path, framerate="100"):
        self.corrupted = Path(corrupted_path)
        self.reference = Path(reference_path)
        self.framerate = framerate
        
        self.valid_headers = Path("valid_headers.hevc")
        self.cleaned_stream = Path("cleaned_stream.hevc")
        self.final_output = self.corrupted.with_name(f"{self.corrupted.stem}_Native_Recovered.mp4")

    def execute(self):
        print(f"Initializing Native Recovery for: {self.corrupted.name}")
        try:
            self._extract_reference_headers()
            self._parse_and_clean_bitstream()
            self._remux_final_container()
            print(f"\nSuccess. Final file: {self.final_output}")
        except Exception as e:
            print(f"\nRecovery Failed: {e}")
        finally:
            self._cleanup()

    def _extract_reference_headers(self):
        print("Step 1/3: Extracting Annex B headers from reference file...")
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-i", str(self.reference),
            "-c:v", "copy",
            "-bsf:v", "hevc_mp4toannexb",
            "-frames:v", "1",
            str(self.valid_headers)
        ], check=True)

    def _sync_to_video_stream(self, f_in):
        """Scans forward in 64KB chunks to find a valid HEVC NAL unit signature."""
        chunk_size = 65536
        buffer = f_in.read(chunk_size)
        offset = f_in.tell() - len(buffer)
        
        while buffer:
            # Scan through the chunk looking for a valid length + HEVC header
            for i in range(len(buffer) - 8):
                first_4 = struct.unpack('>I', buffer[i:i+4])[0]
                
                # NAL size must be sane (between 255 bytes and 16MB)
                if 0x000000FF < first_4 < 0x01000000:
                    next_2 = buffer[i+4:i+6]
                    # Check against known DJI HEVC/H.264 frame signatures
                    if next_2 in (b'\x28\x01', b'\x26\x01', b'\x02\x01', b'\x65\xb8'):
                        actual_offset = offset + i
                        f_in.seek(actual_offset)
                        print(f"\n[+] Locked onto video stream at offset {hex(actual_offset)}")
                        return True
            
            # Read next chunk, overlapping by 8 bytes to avoid splitting a signature
            f_in.seek(offset + len(buffer) - 8)
            offset = f_in.tell()
            buffer = f_in.read(chunk_size)
            
        return False

    def _parse_and_clean_bitstream(self):
        print("Step 2/3: Parsing binary stream and stripping DJI telemetry...")
        
        with open(self.corrupted, 'rb') as f_in, open(self.cleaned_stream, 'wb') as f_out:
            # Pre-append the valid reference headers
            with open(self.valid_headers, 'rb') as f_headers:
                f_out.write(f_headers.read())

            # 1. Skip Container Atoms to Mdat
            while True:
                size_bytes = f_in.read(4)
                if not size_bytes:
                    raise EOFError("Reached end of file without finding mdat.")
                
                atom_size = struct.unpack('>I', size_bytes)[0]
                atom_type = f_in.read(4)
                
                if atom_type == b'mdat':
                    break 
                f_in.seek(atom_size - 8, os.SEEK_CUR)

            # 2. Slide window through the padding to find the first frame
            if not self._sync_to_video_stream(f_in):
                raise ValueError("Could not find a valid HEVC frame signature in the payload.")

            # 3. Parse NAL Units and Mask Telemetry
            start_code = b'\x00\x00\x00\x01'
            bytes_processed = 0

            while True:
                size_bytes = f_in.read(4)
                if not size_bytes or len(size_bytes) < 4:
                    break
                
                nal_size = struct.unpack('>I', size_bytes)[0]

                # If size is impossible, we hit corruption mid-stream. Resync.
                if nal_size == 0 or nal_size > 0x01000000:
                    print(f"\n[!] Anomaly detected at {hex(f_in.tell() - 4)}. Attempting to resync...")
                    if not self._sync_to_video_stream(f_in):
                        print("[-] Could not resync. Ending extraction.")
                        break
                    continue
                
                # Telemetry Bitwise Masking
                high_16 = nal_size & 0xFFFF0000
                high_8 = nal_size & 0xFF000000
                
                if high_16 == 0x01FE0000:
                    f_in.seek(0x200 - 4, os.SEEK_CUR)
                elif high_8 == 0x12800000:
                    block_size = ((nal_size >> 16) - 0x12a5) + 0x46A9
                    f_in.seek(block_size - 4, os.SEEK_CUR)
                elif high_16 in (0x211C0000, 0x2ECF0000, 0x38110000, 0x5D9C0000, 0x5DBB0000, 0x80210000):
                    f_in.seek(0x1F9 - 4, os.SEEK_CUR)
                elif nal_size == 0x05c64e6f:
                    f_in.seek(0x05c6, os.SEEK_CUR)
                elif nal_size == 0x00fe462f:
                    f_in.seek(0x100 - 4, os.SEEK_CUR)
                elif high_16 == 0x1A2D0000:
                    f_in.seek(0x2F + (nal_size & 0x0000FFF0) - 0x0A00 - 4, os.SEEK_CUR)
                elif (nal_size & 0xFFFE0000) == 0x1A2E0000:
                    offset = 1 if (nal_size & 0x00010000) else 0
                    f_in.seek(0x30 + offset - 4, os.SEEK_CUR)
                elif (nal_size & 0xFFF00000) == 0x1A700000:
                    f_in.seek(0x79 + (nal_size >> 16) - 0x1A77 - 4, os.SEEK_CUR)
                elif high_8 == 0x1A800000:
                    block_size = (nal_size >> 16) - 0x177d
                    f_in.seek(block_size - 4, os.SEEK_CUR)
                else:
                    # Valid Video NAL Unit
                    payload = f_in.read(nal_size)
                    f_out.write(start_code)
                    f_out.write(payload)
                    bytes_processed += nal_size

                    # Console progress indicator (dot every ~100MB processed)
                    if bytes_processed % (100 * 1024 * 1024) < nal_size:
                        sys.stdout.write(".")
                        sys.stdout.flush()

    def _remux_final_container(self):
        print("\nStep 3/3: Muxing final container and reconstructing Presentation Time Stamps (PTS)...")
        subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-fflags", "+igndts+genpts",
            "-framerate", self.framerate,
            "-i", str(self.cleaned_stream),
            "-c:v", "copy",
            "-tag:v", "hvc1",
            str(self.final_output)
        ], check=True)

    def _cleanup(self):
        print("Executing workspace cleanup...")
        for temp_file in [self.valid_headers, self.cleaned_stream]:
            if temp_file.exists():
                temp_file.unlink()

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python native_dji_recover.py <corrupted.mp4> <reference.mp4> [framerate]")
        sys.exit(1)
        
    bad_file = sys.argv[1]
    good_file = sys.argv[2]
    fps = sys.argv[3] if len(sys.argv) > 3 else "100"
    
    recovery_tool = NativeDJIRecover(bad_file, good_file, fps)
    recovery_tool.execute()