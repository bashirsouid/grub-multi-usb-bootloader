# GRUB2 Multiboot USB Creator

A command-line Python tool for creating secure, auditable multiboot USB drives using GRUB2 bootloader. All code is transparent—uses only standard Linux tools and plain-text configuration.

**Note:** Currently this project is under active development. Test thoroughly before using in production workflows.

## Why GRUB2?

Unlike Ventoy:
- **No binary blobs**: All code is auditable Python
- **Standard tools only**: Uses parted, mount, mkfs, grub-install from your distro
- **Transparent**: Plain-text GRUB2 configuration
- **Proven**: GRUB2 is battle-tested on millions of systems

## Features

- **Dual mode**: Interactive prompts OR command-line automation
- **Safe by default**: Dry-run mode, lists all disks, confirms before changes
- **Flexible**: Create GRUB-only USB OR add ISO files later
- **Optional ISOs**: `--iso-dir` is optional; create empty USB and drag-and-drop ISOs manually
- **Large file support**: Optional exFAT partition for ISOs >4GB
- **Minimal boot partition**: Default 256 MB (just enough for GRUB + config)

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

### Option 1: With ISO Files

```bash
# Prepare ISOs
mkdir ~/multiboot-isos
cp ~/Downloads/*.iso ~/multiboot-isos/

# Dry-run preview (safe)
python3 main.py --iso-dir ~/multiboot-isos

# Execute (requires sudo)
sudo python3 main.py --iso-dir ~/multiboot-isos --device /dev/sdb --auto-confirm --no-dry-run
```

### Option 2: Empty GRUB-Only USB (Add ISOs Later)

```bash
# Dry-run preview
python3 main.py --device /dev/sdb --dry-run

# Execute (requires sudo)
sudo python3 main.py --device /dev/sdb --auto-confirm --no-dry-run
```

Then manually copy ISOs to the USB:
```bash
mount /dev/sdb2 /mnt/usb-iso
cp ~/Downloads/*.iso /mnt/usb-iso/isos/
umount /mnt/usb-iso
```

## Usage

### Interactive Mode (Recommended)

```bash
python3 main.py
```

The script will:
1. List all USB devices and prompt for selection
2. Show ISOs found (if `--iso-dir` provided)
3. Display all planned operations
4. Require you to confirm before making changes

### Fully Automated (with ISOs)

```bash
sudo python3 main.py \
  --iso-dir ~/multiboot-isos \
  --device /dev/sdb \
  --auto-confirm \
  --no-dry-run
```

### Fully Automated (empty USB)

```bash
sudo python3 main.py \
  --device /dev/sdb \
  --auto-confirm \
  --no-dry-run
```

### Dry-Run Preview (Safe)

```bash
python3 main.py --iso-dir ~/multiboot-isos --device /dev/sdb --auto-confirm
```

This shows all operations without executing them.

### Command-Line Options

```
--iso-dir, -i              Directory with ISO files (optional)
--device, -d               USB device path (e.g., /dev/sdb)
--mount-point, -m          Mount point for USB (default: /mnt/usb)
--boot-size-mb             Boot partition size in MB (default: 256)
--iso-format               ext4 or exfat (default: ext4)
--dry-run                  Preview mode (default: enabled)
--no-dry-run               Execute changes (destructive! requires root)
--auto-confirm             Skip confirmation prompts (for automation)
--help, -h                 Show help message
```

## Workflow

### Fresh USB Setup with ISOs

1. **Wipe device** - Removes all data
2. **Create partitions**:
   - Partition 1: Boot (ext4, default 256 MB)
   - Partition 2: ISOs (ext4 or exFAT, remaining space)
3. **Mount partitions**
4. **Install GRUB2** - Installs bootloader to boot sector
5. **Copy ISOs** - Copies all ISO files to `/isos` folder
6. **Generate GRUB config** - Creates `grub.cfg` with multiboot menu entries
7. **Unmount** - Safely ejects USB

### Fresh USB Setup (Empty, Add ISOs Later)

Same as above, but step 5 is skipped. The `/isos` folder is created but empty.

## Partition Layout

```
/dev/sdbX (Master Boot Record)
├── /dev/sdb1 (Boot, ext4, 256 MB)
│   ├── /boot/grub/              (GRUB2 files)
│   └── /boot/grub/grub.cfg      (Menu configuration)
└── /dev/sdb2 (ISOs, ext4, ~remaining)
    └── /isos/                   (ISO files folder)
        ├── ubuntu-24.04.iso     (optional - add manually or via --iso-dir)
        ├── debian-12.iso
        └── ...
```

## GRUB Configuration

The generated `grub.cfg` includes:

- **Multiboot menu entries** for each ISO (if any present)
- **Loopback mounting** for efficient kernel loading
- **System utilities**: UEFI firmware settings, reboot, shutdown
- **Timeout**: 10 seconds (editable in `/boot/grub/grub.cfg`)

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

GRUB2 on this USB may prompt for MOK (Machine Owner Key) enrollment:

1. Boot USB in UEFI mode
2. GRUB menu appears
3. Select "Enroll MOK" (if prompted)
4. Complete enrollment flow
5. Reboot to use

For detailed guidance: [Ubuntu Secure Boot Documentation](https://wiki.ubuntu.com/SecureBoot)

## Large ISOs (>4GB)

ext4 has a 4GB file size limit. For larger ISOs:

```bash
sudo python3 main.py --iso-dir ~/multiboot-isos --iso-format exfat --no-dry-run
```

This creates the ISO partition as exFAT instead, supporting files >4GB.

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

### Permission Denied (--no-dry-run)

```
✗ --no-dry-run requires root.
```

**Solution**: Use `sudo` when executing changes:

```bash
sudo python3 main.py --device /dev/sdb --no-dry-run --auto-confirm
```

### ISO Not Booting

1. Verify ISO integrity:
   ```bash
   sha256sum ~/multiboot-isos/*.iso
   ```

2. Check if file was copied:
   ```bash
   mount /dev/sdb2 /mnt/usb-iso
   ls -lh /mnt/usb-iso/isos/
   umount /mnt/usb-iso
   ```

3. Verify GRUB config:
   ```bash
   mount /dev/sdb1 /mnt/usb-boot
   cat /mnt/usb-boot/grub/grub.cfg
   umount /mnt/usb-boot
   ```

4. Some distros require custom kernel parameters—edit `grub.cfg` manually if needed.

### Stuck in Dry-Run

Running with `--dry-run` (default) performs no operations. To execute:

```bash
sudo python3 main.py --device /dev/sdb --no-dry-run --auto-confirm
```

## Adding ISOs Manually

If you created an empty USB, you can add ISOs later:

```bash
# Mount the ISO partition
sudo mount /dev/sdb2 /mnt/iso-partition

# Copy ISOs
cp ~/Downloads/*.iso /mnt/iso-partition/isos/

# Update grub.cfg (optional - GRUB will auto-detect ISOs)
# Edit /mnt/usb-boot/grub/grub.cfg if needed

# Unmount
sudo umount /mnt/iso-partition
```

## Security & Auditing

- **No network calls** - Fully offline operation
- **No telemetry** - Code doesn't phone home
- **Source code available** - All code is plain-text Python (~450 lines)
- **Standard tools only** - Uses GRUB2, parted, mkfs from your distro
- **Reproducible** - Same inputs produce identical results

To audit the code:
```bash
python3 -m py_compile main.py  # Syntax check
grep -n "subprocess" main.py   # View all external commands
wc -l main.py                  # Line count
```

## License

GNU General Public License v2.0 (GPLv2)

See LICENSE file for details.

## Related Resources

- [GRUB2 Manual - Loopback Booting](https://www.gnu.org/software/grub/manual/grub/grub.html#Loopback-booting)
- [Linux Bootstick Guide](https://rikublock.dev/docs/tutorials/linux-bootstick/)
- [Secure Boot & MOK Manager](https://wiki.ubuntu.com/SecureBoot)

---

**Last Updated**: 2025-12-25  
**License**: GPLv2  
**Status**: Development
