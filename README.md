# extract-paradox-hive

Extract Paradox Anticheat's custom "data hive" from a Minecraft Bedrock Edition save and write it out as plain JSON.

Paradox stores its configuration and state as *dynamic properties* inside the world's LevelDB. Those properties are split into chunks, indexed by pointer entries, and LZW-compressed, so they are unreadable if you just open the database. This tool reads the raw properties, reassembles the chunks, decompresses the payloads and gives you one structured JSON document.

- Works on a world **folder**, a **`.zip`**, or a **`.mcworld`** export
- Never crashes on a bad payload: corrupt entries are kept as an error record with the raw data instead
- Archives are unpacked to a temporary directory that is always deleted afterwards
- Small, typed, dependency-light codebase (the only runtime dependency is [`amulet-leveldb`](https://pypi.org/project/amulet-leveldb/)) with a test suite that needs no real database

---

## Contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Usage](#usage)
- [Output format](#output-format)
- [How it works](#how-it-works)
- [Using it as a library](#using-it-as-a-library)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Development](#development)
- [Project layout](#project-layout)

---

## Requirements

- Python **3.12 or newer**
- [`amulet-leveldb`](https://pypi.org/project/amulet-leveldb/) `>=1.0.7,<2.0.0` (installed automatically)

## Installation

With pip:

```bash
pip install extract-paradox-hive
```

From a clone of the repository (local development):

```bash
git clone https://github.com/SeaMooLab/extract-paradox-hive.git
cd extract-paradox-hive
poetry install
```

If `amulet-leveldb` is missing at runtime the tool stops with a clear message (`pip install amulet-leveldb`) rather than a traceback.

## Usage

```text
hive-extractor <input_world_or_zip> <output_file.json>
```

Equivalent ways to run it:

```bash
poetry run hive-extractor MyWorld.mcworld hive.json   # installed console script
python -m extract_paradox_hive MyWorld.mcworld hive.json
```

`<input_world_or_zip>` can be:

| Input | What happens |
| --- | --- |
| A directory | Searched top-down for the first folder named `db`, used in place |
| A `.zip` or `.mcworld` file | Unpacked to a temporary directory, searched for `db`, then the directory is deleted |

Anything else (a missing path, another file type, or an input with no `db` folder) is an error.

> **Work on a copy.** Opening a LevelDB can write to its directory (lock and log files, and possibly recovery or compaction work). The extractor only reads data, but the database engine itself is not guaranteed to leave the folder byte-for-byte untouched. Point it at an exported `.mcworld` or a copy of your world rather than a live save. Archives are always safe, since they are unpacked to a temporary directory first.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success, JSON written |
| `1` | Missing arguments, `amulet-leveldb` not installed, bad input path, or any extraction/write failure |

### Example session

```text
$ hive-extractor MyWorld.mcworld hive.json
[Harness] Target is an archive. Extracting...
[Harness] Extracted to temporary workspace: /tmp/bedrock-extractor-t07evrvj
[Harness] Found LevelDB at: /tmp/bedrock-extractor-t07evrvj/db
[LevelDB] Scanning database...
[LevelDB] Found global 'DynamicProperties' NBT blob. Decrypting NBT...
[LevelDB] Found 4 raw string properties.
[Warning] Corrupted payload caught for 'example/broken' - Dumping raw data.

[Success] Data Hive extracted and saved to: /path/to/hive.json
[Harness] Cleaning up temporary workspace...
```

Progress and diagnostics go to stdout, tagged `[Harness]`, `[LevelDB]`, `[Warning]`, `[Error]`, `[Fatal]` or `[Success]`. Warnings never abort a run.

## Output format

The output is a single JSON object, indented with 4 spaces and UTF-8 encoded (non-ASCII characters are written as `\uXXXX` escapes, which any JSON parser reads back correctly).

```json
{
    "example": {
        "settings": {
            "enabled": true,
            "limits": [1, 2, 3]
        },
        "broken": {
            "__ERROR__": "Decompression Failed",
            "__REASON__": "Invalid dictionary index: 9999 (dict_size: 256)",
            "__RAW_PAYLOAD__": "[65, 9999]",
            "__HEX__": "5b36352c20393939395d"
        }
    },
    "__GLOBAL_PROPERTIES__": {
        "standalone_flag": true
    }
}
```

(Compact arrays shown for readability; the real file indents every element.)

| Top-level key | Contents |
| --- | --- |
| `<namespace>` | One object per namespace that publishes a `<namespace>/pointers` entry. Each key inside is the **last path segment** of an active entry, and each value is that entry decoded |
| `__GLOBAL_PROPERTIES__` | Properties that no namespace claimed. Present only if there are any. Values are parsed as JSON when possible, otherwise kept as text |

Each entry value is one of:

| Value | When |
| --- | --- |
| Parsed JSON (object, array, number, string, bool) | The decompressed text is valid JSON |
| A string | It decompressed fine but is not JSON |
| `null` | It decompressed to blank text |
| An error record | Decompression failed (see below) |

An **error record** always has these four fields, so you can still recover or inspect the data by hand:

| Field | Meaning |
| --- | --- |
| `__ERROR__` | Always `"Decompression Failed"` |
| `__REASON__` | The underlying error message |
| `__RAW_PAYLOAD__` | The payload text after any header was stripped |
| `__HEX__` | The same payload as UTF-8 hex (undecodable characters replaced) |

Namespaces are emitted in sorted order, so output is deterministic for a given database.

## How it works

The extraction runs in stages, each implemented in its own module.

1. **Locate** (`world_input`): resolve the input to a `db` folder, unpacking archives into a temp directory that is removed on exit, even on error.
2. **Read** (`hive_extractor`): collect every string property from the LevelDB into one flat `key -> string` map:
   - The global `DynamicProperties` entry is parsed as little-endian NBT and every `TAG_String` is harvested by tag name, flattened across nesting levels (`bedrock_nbt`).
   - Every other entry is then swept. A key is kept if, after reducing it to its printable ASCII characters, it is at least 3 characters long and contains a letter. A value is kept if it is valid UTF-8 and does not contain two consecutive NUL characters, which is how binary chunk data is recognised and skipped. Swept entries override NBT strings with the same name.
3. **Assemble** (`hive_assembler`): a pure function of that map, with no database access:
   - Any key containing `/pointers` marks a namespace (the text before it).
   - A pointer entry holds a JSON **list of keys** that are active for that namespace.
   - Values may be stored whole under `<key>`, or split across `<key>/0`, `<key>/1`, ... which are concatenated in order. Chunked storage wins if both exist. Numbering must be contiguous; stitching stops at the first gap.
   - An optional header (`\x02` ... `:`) at the start of a payload is stripped.
   - What remains is a JSON array of integer **LZW codes**, decompressed by `lz_compressor` and then parsed as JSON where possible.
   - Everything left unclaimed becomes `__GLOBAL_PROPERTIES__`.
4. **Write** (`cli`): dump the result as JSON.

## Using it as a library

Everything the CLI does is available as plain functions and classes.

```python
from pathlib import Path

from extract_paradox_hive.cli import extract_hive, write_hive
from extract_paradox_hive.world_input import locate_database

with locate_database(Path("MyWorld.mcworld")) as db_path:
    hive = extract_hive(db_path)

write_hive(hive, Path("hive.json"))
print(hive.get("__GLOBAL_PROPERTIES__", {}))
```

`locate_database` raises `WorldInputError` for bad input. Pieces can also be used on their own:

```python
from extract_paradox_hive.hive_assembler import HiveAssembler
from extract_paradox_hive.hive_extractor import BedrockHiveExtractor
from extract_paradox_hive.lz_compressor import LZCompressor
from extract_paradox_hive.bedrock_nbt import BedrockNBT

# Database access, closed automatically
with BedrockHiveExtractor("/path/to/db") as extractor:
    raw = extractor.scan_for_dynamic_properties()   # flat key -> str map

# Interpretation only; works on any dict, no database needed
hive = HiveAssembler(raw).assemble()

# Building blocks
LZCompressor.decompress("[104, 105]")               # -> "hi"
BedrockNBT.extract_strings(nbt_bytes)               # -> {"tag name": "value", ...}
```

| Raises | When |
| --- | --- |
| `LevelDBUnavailableError` (an `ImportError`) | `amulet-leveldb` is not installed (`load_leveldb()`, `BedrockHiveExtractor.open()`) |
| `RuntimeError` | A read is attempted on an extractor that was never opened or has been closed |
| `WorldInputError` | The input path is missing, unsupported, unreadable, or has no `db` folder |
| `ValueError` | `LZCompressor.decompress` got invalid JSON, a null or non-integer code, or a code that is not in the dictionary |

## Troubleshooting

**`[Fatal] amulet-leveldb is not installed`**
Run `pip install amulet-leveldb` (or `poetry install`) in the environment you are running from.

**`[Error] Could not locate a 'db' folder`**
The input contains no folder named exactly `db`. For a world folder, point at the world root (the one containing `level.dat` and `db/`). For an archive, make sure it really is a Bedrock world export.

**`[Error] Unsupported input type`**
Only directories, `.zip` and `.mcworld` are accepted. The suffix match is case-sensitive, so rename `WORLD.ZIP` to `world.zip`.

**`[Error] Failed to extract archive`**
The file is not a valid zip. `.mcworld` files are zip archives, so a truncated download will fail here.

**`[Warning] Corrupted payload caught for '<namespace>/<entry>'`**
Not fatal. That entry is written as an error record containing `__RAW_PAYLOAD__` and `__HEX__`. Typical causes are a damaged or partially written value, or text containing characters above `U+00FF`, which the LZW dictionary is not seeded for (reported as a null code in `__REASON__`).

**`[Warning] No pointer data found for namespace` / `Failed to parse pointers`**
A namespace was detected from a `.../pointers` key, but its pointer entry was blank or was not a JSON list. The namespace appears in the output as `{}`.

**The output is empty, or has no entries for the namespace you expect**
Only properties stored as readable text are found. Check that the world actually has Paradox data and that you are pointing at the right save.

## Limitations

- The storage conventions (pointers, numbered chunks, the optional `\x02` header, LZW codes) are inferred from observed data rather than from a published specification. If Paradox changes its format, extraction may need updating.
- Chunk stitching stops at the first missing number, so a value with a gap in its chunk sequence is truncated at the gap.
- Strings in NBT are keyed by tag name only, flattened across nesting, so two tags with the same name at different depths collide (the later one wins).
- This is a read-only extraction tool. It does not write anything back into a world.

## Development

Dev tooling is declared as a `dev` dependency group (and a `dev` extra, so `pip install -e ".[dev]"` also works).

```bash
poetry install                 # add --with dev if your Poetry treats the group as optional
poetry run pytest              # tests + coverage (fails below 95%), HTML report in htmlcov/
poetry run pyright             # type checking
poetry run black .             # formatting
```

The test suite is written with `unittest`-style cases, so it also runs with no extra dependencies:

```bash
python -m unittest discover -s tests -t .
```

No real LevelDB is needed to test. `tests/helpers.py` provides a dict-backed `FakeLevelDB`, a little-endian NBT builder, and a reference LZW compressor for generating payloads.

### Typing notes

- `typings/leveldb/` holds local type stubs for `amulet-leveldb` (configured via `stubPath` in `pyproject.toml`). Names are re-exported with `X as X` and `__all__`, because pyright treats a plain `from x import y` in a `.pyi` file as private.
- `BedrockHiveExtractor` depends on the small `LevelDBHandle` protocol (`get` and `close`) rather than the concrete class, so test fakes type-check without casts.

### Design principles

- **Single responsibility:** each module does one job (locate, read, assemble, decompress, parse NBT, wire together).
- **Pure core:** `HiveAssembler` and `LZCompressor` never touch the filesystem or database, so they are tested with plain strings and dicts.
- **Resources are always released:** databases close and temp directories are deleted via context managers, including on errors.
- **Test first:** behavior changes start as a failing test.

## Project layout

```text
extract_paradox_hive/
├── __main__.py        # python -m extract_paradox_hive
├── cli.py             # argument handling, exit codes, JSON output
├── world_input.py     # directory/.zip/.mcworld -> db folder (temp dir cleanup)
├── hive_extractor.py  # LevelDB access: global NBT blob + entry sweep
├── hive_assembler.py  # pointers, chunk stitching, headers, orphans (pure)
├── lz_compressor.py   # LZW decompression of code arrays (pure)
└── bedrock_nbt.py     # little-endian NBT TAG_String harvester (pure)
typings/leveldb/       # local stubs for amulet-leveldb
tests/                 # one test module per source module, plus packaging checks
```
