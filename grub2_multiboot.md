# GRUB2 Multiboot USB Creator

A command-line Python tool for creating secure, auditable GRUB2-based multiboot USB drives. All code is transparent—uses only standard Linux tools and plain-text configuration.

## Quick Start

```bash
python3 main.py --iso-dir ~/isos --device /dev/sdb
```

Runs in dry-run mode by default (safe preview). See all planned operations before execution.

## Files

- **main.py** - Main application (executable)
- **requirements.txt** - Python dependencies (minimal)
- **README.md** - Full documentation

## Installation

```bash
pip install -r requirements.txt
python3 main.py --help
```

## Usage Examples

### Interactive Mode (Recommended First Time)
```bash
python3 main.py --iso-dir ~/isos
```
Will prompt for USB device and confirm before making changes.

### Automated (Dry-Run Preview)
```bash
python3 main.py --iso-dir ~/isos --device /dev/sdb --auto-confirm --dry-run
```

### Execute (No Dry-Run)
```bash
python3 main.py --iso-dir ~/isos --device /dev/sdb --auto-confirm --no-dry-run
```

### Full Options
```bash
python3 main.py \
  --iso-dir ~/isos \
  --device /dev/sdb \
  --mount-point /mnt/usb \
  --boot-size 2 \
  --iso-format ext4 \
  --auto-confirm \
  --dry-run
```

## Key Features

- **Dual Mode**: Interactive prompts OR command-line automation
- **Safe**: Dry-run by default, lists all disks, confirms device
- **Flexible**: Fresh install OR update ISOs on existing USB
- **Large Files**: Optional exFAT support for ISOs >4GB
- **Transparent**: Uses standard tools (parted, mount, grub-install)
- **Idempotent**: Can detect and preserve existing GRUB2 installations

## Security

All operations use standard Linux tools. No proprietary binaries, no closed-source code. Fully auditable Python.

## Troubleshooting

See README.md for detailed troubleshooting, Secure Boot guidance, and infrastructure automation examples.