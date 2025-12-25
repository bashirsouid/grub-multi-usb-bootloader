# GRUB2 Multiboot USB Creator

A command-line Python tool for creating secure, auditable multiboot USB drives using GRUB2 bootloader. All code is transparent—uses only standard Linux tools and plain-text configuration.

## Why GRUB2?

Unlike Ventoy:
- **No binary blobs**: All code is auditable Python
- **Standard tools only**: Uses parted, mount, mkfs, grub-install from your distro
- **Transparent**: Plain-text GRUB2 configuration
- **Proven**: GRUB2 is battle-tested on millions of systems

## Features

- **Dual mode**: Interactive prompts OR command-line automation
- **Safe by default**: Dry-run mode, lists all disks, confirms before changes
- **Flexible**: Fresh install OR update ISOs on existing USB
- **Large file support**: Optional exFAT partition for ISOs >4GB
- **Idempotent**: Can detect and preserve existing GRUB2 installations

## Installation

### Dependencies

**Debian/Ubuntu:**
```bash
sudo apt-get install grub-pc-bin parted e2fsprogs
```

**Fedora/RHEL:**
```bash
sudo dnf install grub2-tools parted e2fsprogs
```

**Arch Linux:**
```bash
sudo pacman -S grub parted e2fsprogs
```

**Python dependencies:**
```bash
pip install -r requirements.txt
```

## Quick Start

### 1. Prepare ISOs

```bash
mkdir ~/multiboot-isos
cp ~/Downloads/*.iso ~/multiboot-isos/
```

### 2. Run in Dry-Run Mode (Safe Preview)

```bash
python3 main.py --iso-dir ~/multiboot-isos
```

The script will:
1. List all USB devices (confirm selection)
2. Show ISO files found
3. Display all operations planned
4. **Stop before making any changes**

### 3. Execute (When Ready)

```bash
python3 main.py --iso-dir ~/multiboot-isos --device /dev/sdb --auto-confirm --no-dry-run
```

## Usage

### Interactive Mode (Recommended)

```bash
python3 main.py --iso-dir ~/multiboot-isos
```

Prompts for:
- USB device selection (with sizes)
- Confirmation before formatting

### Fully Automated

```bash
python3 main.py \
  --iso-dir ~/multiboot-isos \
  --device /dev/sdb \
  --mount-point /mnt/usb \
  --boot-size 2 \
  --iso-format ext4 \
  --auto-confirm \
  --dry-run
```

Remove `--dry-run` to execute:

```bash
python3 main.py \
  --iso-dir ~/multiboot-isos \
  --device /dev/sdb \
  --auto-confirm \
  --no-dry-run
```

### Command-Line Options

```
--iso-dir, -i           Directory containing ISO files (required)
--device, -d            USB device path (e.g., /dev/sdb)
--mount-point, -m       Mount point for USB (default: /mnt/usb)
--boot-size             Boot partition size in GB (default: 2)
--iso-format            ext4 or exfat (default: ext4)
--dry-run               Preview mode (default: enabled)
--no-dry-run            Execute changes (destructive!)
--auto-confirm          Skip confirmation prompts (for automation)
--help, -h              Show help message
```

## Workflow

### Fresh USB Setup

1. **Wipe device** - Removes all data
2. **Create partitions**:
   - Partition 1: Boot (ext4, user-specified size, default 2GB)
   - Partition 2: ISOs (ext4 or exFAT, remaining space)
3. **Mount partitions**
4. **Install GRUB2** - Installs bootloader to boot sector
5. **Copy ISOs** - Copies all ISO files to ISO partition
6. **Generate GRUB config** - Creates grub.cfg with multiboot menu entries
7. **Unmount** - Safely ejects USB

### Updating Existing USB

If the device already has GRUB2 installed, the script will:
- Detect existing installation
- Skip format/partition steps
- Update ISOs and regenerate GRUB configuration

## Partition Layout

```
/dev/sdbX (Master Boot Record)
├── /dev/sdb1 (Boot, ext4, 2GB)
│   ├── /boot/grub/              (GRUB2 files)
│   └── /boot/grub/grub.cfg      (Menu configuration)
└── /dev/sdb2 (ISOs, ext4, ~remaining)
    └── /isos/                   (ISO files folder)
        ├── ubuntu-24.04.iso
        ├── debian-12.iso
        └── ...
```

## GRUB Configuration

The generated `grub.cfg` includes:

- **Multiboot menu entries** for each ISO (one per file)
- **Loopback mounting** for efficient kernel loading
- **System utilities**: UEFI firmware settings, reboot, shutdown
- **Timeout**: 10 seconds (editable)

Example menu entry:

```
menuentry "Ubuntu 24.04 LTS" {
    echo "Loading Ubuntu 24.04 LTS..."
    set isofile=/isos/ubuntu-24.04-live-server-amd64.iso
    loopback loop $isofile
    linux (loop)/casper/vmlinuz iso-scan/filename=$isofile boot=casper noeject noprompt splash --
    initrd (loop)/casper/initrd
}
```

## Secure Boot

### Legacy BIOS / UEFI (Secure Boot Disabled)

No special configuration needed. Works out-of-the-box.

### UEFI with Secure Boot Enabled

GRUB2 on this USB will prompt for MOK (Machine Owner Key) enrollment:

1. Boot USB in UEFI mode
2. GRUB starts normally
3. Select "Enroll MOK" (if prompted)
4. Follow enrollment steps
5. Reboot to use

For detailed guidance: [Ubuntu Secure Boot Documentation](https://wiki.ubuntu.com/SecureBoot)

## Large ISOs (>4GB)

ext4 has a 4GB file size limit. For larger ISOs:

```bash
python3 main.py --iso-dir ~/multiboot-isos --iso-format exfat
```

This creates the ISO partition as exFAT instead.

## Troubleshooting

### Device Not Found

```
✗ Device not found: /dev/sdb
```

**Solution**: Connect USB and check available devices:

```bash
lsblk
```

Then specify the correct device path.

### Permission Denied

```
✗ Command failed: ['sudo', 'parted', ...]
```

**Solution**: Script uses `sudo` for privileged operations. You may see a password prompt.

If passwordless sudo is configured:

```bash
sudo python3 main.py --iso-dir ~/isos --device /dev/sdb --no-dry-run --auto-confirm
```

### ISO Not Booting

1. Verify ISO integrity:
   ```bash
   sha256sum ~/multiboot-isos/*.iso
   ```

2. Check if file was copied:
   ```bash
   ls -lh /mnt/usb/isos/
   ```

3. Verify GRUB config:
   ```bash
   cat /mnt/usb/boot/grub/grub.cfg
   ```

4. Some distros require custom kernel parameters—edit `grub.cfg` manually if needed.

### Stuck in Dry-Run

Running with `--dry-run` (default). Remove it:

```bash
python3 main.py --iso-dir ~/isos --device /dev/sdb --no-dry-run --auto-confirm
```

## Security & Auditing

- **No network calls** - Fully offline operation
- **No telemetry** - Code doesn't phone home
- **Source code available** - All code is plain-text Python
- **Standard tools only** - Uses GRUB2, parted, mkfs from your distro
- **Reproducible** - Same inputs produce identical results

## License

GNU General Public License v2.0 (GPLv2)

## Related Resources

- [GRUB2 Manual - Loopback Booting](https://www.gnu.org/software/grub/manual/grub/grub.html#Loopback-booting)
- [Linux Bootstick Guide](https://rikublock.dev/docs/tutorials/linux-bootstick/)
- [Secure Boot & MOK Manager](https://wiki.ubuntu.com/SecureBoot)

---

**Last Updated**: 2025-12-24  
**License**: GPLv2
