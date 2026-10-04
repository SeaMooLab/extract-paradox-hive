#!/usr/bin/env python3
import os
import sys
import json
import shutil
import tempfile
import zipfile
import re
from pathlib import Path

try:
    from leveldb import LevelDB
except ImportError:
    print("[Fatal] amulet-leveldb is not installed. Run: pip install amulet-leveldb")
    sys.exit(1)

class LZCompressor:
    """
    UTF-16 Safe LZW Compressor translated for Python.
    Translates repetitive strings/JSON patterns back from compact arrays.
    """
    @staticmethod
    def decompress(compressed_str: str) -> str:
        if not compressed_str:
            return ""
        try:
            codes = json.loads(compressed_str)
            if not isinstance(codes, list) or not codes:
                return ""
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON payload: {e}")

        dictionary = {i: chr(i) for i in range(256)}
        dict_size = 256
        
        if codes[0] is None:
            raise ValueError("Initial code is null")
            
        w = chr(codes[0])
        result = [w]
        
        for k in codes[1:]:
            if k is None:
                raise ValueError("Null code encountered in stream (Likely unseeded Unicode char)")
                
            if k in dictionary:
                entry = dictionary[k]
            elif k == dict_size:
                entry = w + w[0]
            else:
                raise ValueError(f"Invalid dictionary index: {k} (dict_size: {dict_size})")
                
            result.append(entry)
            dictionary[dict_size] = w + entry[0]
            dict_size += 1
            w = entry
            
        return "".join(result)

class BedrockNBT:
    """
    Lightweight, dependency-free Little-Endian NBT parser.
    Designed specifically to rip Tag_String payloads out of Bedrock's monolithic property blobs.
    """
    @staticmethod
    def extract_strings(data: bytes) -> dict:
        strings = {}
        offset = 0

        def read_string() -> bytes:
            nonlocal offset
            if offset + 2 > len(data): return b""
            length = int.from_bytes(data[offset:offset+2], 'little', signed=False)
            offset += 2
            val = data[offset:offset+length]
            offset += length
            return val

        def read_tag(tag_type: int):
            nonlocal offset
            if tag_type == 1: offset += 1
            elif tag_type == 2: offset += 2
            elif tag_type == 3: offset += 4
            elif tag_type == 4: offset += 8
            elif tag_type == 5: offset += 4
            elif tag_type == 6: offset += 8
            elif tag_type == 7:
                length = int.from_bytes(data[offset:offset+4], 'little', signed=True)
                offset += 4 + length
            elif tag_type == 8:
                return read_string()
            elif tag_type == 9:
                l_type = data[offset]
                offset += 1
                l_len = int.from_bytes(data[offset:offset+4], 'little', signed=True)
                offset += 4
                for _ in range(l_len):
                    read_tag(l_type)
            elif tag_type == 10:
                while offset < len(data):
                    t = data[offset]
                    offset += 1
                    if t == 0: break # Tag_End
                    name = read_string()
                    val = read_tag(t)
                    
                    # Grab ALL Tag_Strings without filtering
                    if t == 8:
                        try:
                            name_str = name.decode('utf-8')
                            strings[name_str] = val.decode('utf-8')
                        except UnicodeDecodeError:
                            pass
            elif tag_type == 11:
                length = int.from_bytes(data[offset:offset+4], 'little', signed=True)
                offset += 4 + (length * 4)
            elif tag_type == 12:
                length = int.from_bytes(data[offset:offset+4], 'little', signed=True)
                offset += 4 + (length * 8)
            return None

        try:
            if len(data) > 0 and data[0] == 10:
                offset += 1
                read_string()
                read_tag(10)
        except Exception as e:
            print(f"[Warning] NBT Parsing hit an exception: {e}")
            
        return strings

class BedrockHiveExtractor:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.db = None

    def open(self):
        self.db = LevelDB(self.db_path)

    def close(self):
        if self.db:
            self.db.close()

    def scan_for_dynamic_properties(self) -> dict:
        raw_properties = {}

        try:
            nbt_blob = self.db.get(b'DynamicProperties')
            if nbt_blob:
                print("[LevelDB] Found global 'DynamicProperties' NBT blob. Decrypting NBT...")
                nbt_strings = BedrockNBT.extract_strings(nbt_blob)
                raw_properties.update(nbt_strings)
        except Exception:
            pass 

        try:
            iterator = getattr(self.db, "items", getattr(self.db, "iterate", lambda: self.db))()
            for item in iterator:
                key_bytes = item[0] if len(item) == 2 else item
                val_bytes = item[1] if len(item) == 2 else self.db.get(key_bytes)

                if not key_bytes or not val_bytes or key_bytes == b'DynamicProperties':
                    continue

                clean_key = "".join([chr(b) for b in key_bytes if 32 <= b <= 126])
                
                # Filter out pure binary chunk keys pretending to be strings
                if len(clean_key) >= 3 and re.search(r'[a-zA-Z]', clean_key):
                    try:
                        val_str = val_bytes.decode("utf-8")
                        # Drop payloads that contain raw sequence nulls (Minecraft binary chunk data)
                        if "\x00\x00" in val_str:
                            continue
                        raw_properties[clean_key] = val_str
                    except UnicodeDecodeError:
                        continue
        except Exception as e:
            print(f"[Warning] Legacy database sweep encountered an error: {e}")
            
        return raw_properties

    def stitch_chunks(self, raw_properties: dict, base_key: str) -> str:
        chunks = []
        i = 0
        while True:
            chunk_key = f"{base_key}/{i}"
            if chunk_key not in raw_properties:
                break
            chunks.append(raw_properties[chunk_key])
            i += 1
        return "".join(chunks)

    def extract(self) -> dict:
        print("[LevelDB] Scanning database...")
        raw_properties = self.scan_for_dynamic_properties()
        print(f"[LevelDB] Found {len(raw_properties)} raw string properties.")

        parsed_hive = {}
        consumed_keys = set()
        
        namespaces = set()
        for k in raw_properties.keys():
            if "/pointers" in k:
                namespaces.add(k.split("/pointers")[0])


        for namespace in namespaces:
            parsed_hive[namespace] = {}
            pointer_base_key = f"{namespace}/pointers"

            pointer_data = self.stitch_chunks(raw_properties, pointer_base_key)
            if not pointer_data:
                pointer_data = raw_properties.get(pointer_base_key)

            if not pointer_data:
                print(f"[Warning] No pointer data found for namespace: {namespace}")
                continue
                
            consumed_keys.add(pointer_base_key)
            i = 0
            while f"{pointer_base_key}/{i}" in raw_properties:
                consumed_keys.add(f"{pointer_base_key}/{i}")
                i += 1

            try:
                active_keys = json.loads(pointer_data)
            except json.JSONDecodeError:
                print(f"[Warning] Failed to parse pointers for namespace: {namespace}")
                continue
                
            for base_key in active_keys:
                stitched_data = self.stitch_chunks(raw_properties, base_key)
                
                if stitched_data:
                    i = 0
                    while f"{base_key}/{i}" in raw_properties:
                        consumed_keys.add(f"{base_key}/{i}")
                        i += 1
                else:
                    stitched_data = raw_properties.get(base_key)
                    if not stitched_data:
                        continue
                    consumed_keys.add(base_key)
                    
                payload = stitched_data
                if stitched_data.startswith("\x02"):
                    header_end = stitched_data.find(":", 2)
                    if header_end != -1:
                        payload = stitched_data[header_end + 1:]
                        
                actual_key = base_key.split("/")[-1]
                
                try:
                    decompressed = LZCompressor.decompress(payload)
                    try:
                        parsed_hive[namespace][actual_key] = json.loads(decompressed) if decompressed.strip() else None
                    except json.JSONDecodeError:
                        parsed_hive[namespace][actual_key] = decompressed
                except ValueError as e:
                    print(f"[Warning] Corrupted payload caught for '{namespace}/{actual_key}' - Dumping raw data.")
                    parsed_hive[namespace][actual_key] = {
                        "__ERROR__": "Decompression Failed",
                        "__REASON__": str(e),
                        "__RAW_PAYLOAD__": payload,
                        "__HEX__": payload.encode('utf-8', errors='replace').hex()
                    }

        # Sweep up anything not claimed by a chunked database
        orphans = {}
        for k, v in raw_properties.items():
            if k not in consumed_keys:
                # One last sanity check to ensure no binary NBT or chunk strings leak through
                if len(k) < 3 or not re.search(r'[a-zA-Z]', k):
                    continue
                if isinstance(v, str) and "\x00\x00" in v:
                    continue
                    
                try:
                    orphans[k] = json.loads(v)
                except json.JSONDecodeError:
                    orphans[k] = v
                    
        if orphans:
            parsed_hive["__GLOBAL_PROPERTIES__"] = orphans

        return parsed_hive

def find_db_folder(start_path: str) -> str:
    for root, dirs, files in os.walk(start_path):
        if os.path.basename(root) == "db":
            return root
    return None

def main():
    if len(sys.argv) < 3:
        print("\nMinecraft Bedrock Data Hive Extractor")
        print("Usage: python extract_hive.py <input_world_or_zip> <output_file.json>\n")
        sys.exit(1)
        
    input_path = Path(sys.argv[1]).resolve()
    output_path = Path(sys.argv[2]).resolve()
    
    if not input_path.exists():
        print(f"[Error] Input path does not exist: {input_path}")
        sys.exit(1)
        
    tmp_dir = None
    target_db_path = None
    
    if input_path.is_dir():
        print("[Harness] Target is a directory. Searching for LevelDB...")
        target_db_path = find_db_folder(str(input_path))
    elif input_path.is_file() and input_path.suffix in [".zip", ".mcworld"]:
        print("[Harness] Target is an archive. Extracting...")
        tmp_dir = tempfile.mkdtemp(prefix="bedrock-extractor-")
        try:
            with zipfile.ZipFile(input_path, 'r') as zip_ref:
                zip_ref.extractall(tmp_dir)
            print(f"[Harness] Extracted to temporary workspace: {tmp_dir}")
            target_db_path = find_db_folder(tmp_dir)
        except Exception as e:
            print(f"[Error] Failed to extract archive: {e}")
            if tmp_dir:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            sys.exit(1)
    else:
        print("[Error] Unsupported input type. Must be a directory, .zip, or .mcworld")
        sys.exit(1)

    if not target_db_path:
        print("[Error] Could not locate a 'db' folder in the provided input.")
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        sys.exit(1)

    print(f"[Harness] Found LevelDB at: {target_db_path}")
    extractor = BedrockHiveExtractor(target_db_path)
    
    try:
        extractor.open()
        data = extractor.extract()
        extractor.close()
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            
        print(f"\n[Success] Data Hive extracted and saved to: {output_path}")
    except Exception as e:
        print(f"\n[Fatal Error] {e}")
    finally:
        if tmp_dir:
            print("[Harness] Cleaning up temporary workspace...")
            shutil.rmtree(tmp_dir, ignore_errors=True)

if __name__ == "__main__":
    main()

