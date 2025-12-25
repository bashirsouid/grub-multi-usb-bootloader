#!/usr/bin/env python3
"""
GRUB2 Multiboot USB Creator

Automates secure, auditable multiboot USB creation using GRUB2 bootloader.
All operations use standard Linux tools: parted, mount, mkfs, grub-install.
"""

import os
import sys
import subprocess
import shutil
import time
from pathlib import Path
from typing import Optional, List, Dict, Tuple


class USBFormatter:
    """Manages USB device operations with safety checks."""

    def __init__(self, device: str, dry_run: bool = True):
        self.device = device
        self.dry_run = dry_run
        self.boot_partition = f"{device}1"
        self.iso_partition = f"{device}2"

    def run_cmd(self, cmd: List[str], needs_sudo: bool = False) -> Tuple[int, str]:
        """Execute command, optionally with sudo/pkexec."""
        if needs_sudo and os.geteuid() != 0:
            cmd = ["sudo"] + cmd

        cmd_str = " ".join(cmd)
        print(f"→ {cmd_str}")

        if self.dry_run:
            print("  [DRY-RUN: skipped]")
            return 0, ""

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.returncode, result.stdout
        except subprocess.CalledProcessError as e:
            print(f"✗ Command failed: {cmd_str}")
            print(f"  Error: {e.stderr}")
            raise
        except FileNotFoundError:
            print(f"✗ Command not found: {cmd[0]}")
            sys.exit(1)

    def list_disks(self) -> List[Dict]:
        """List all block devices with sizes."""
        # Always execute this (not affected by dry_run) - it's just reading state
        try:
            result = subprocess.run(
                ["lsblk", "-bdno", "NAME,SIZE,TYPE"],
                capture_output=True,
                text=True,
                check=True
            )
            output = result.stdout
        except subprocess.CalledProcessError:
            print("✗ Failed to detect disks")
            sys.exit(1)
        except FileNotFoundError:
            print("✗ lsblk not found")
            sys.exit(1)

        devices = []
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 3 and parts[2] == "disk":
                name = parts[0]
                size_bytes = int(parts[1])
                size_gb = size_bytes / (1024 ** 3)
                devices.append({"device": f"/dev/{name}", "size_gb": size_gb})

        return devices

    def confirm_device(self) -> bool:
        """Display disks and confirm device selection."""
        print("\n📋 Available USB devices:")
        devices = self.list_disks()

        for dev in devices:
            marker = "  ← SELECTED" if dev["device"] == self.device else ""
            print(f"   {dev['device']:15} {dev['size_gb']:7.1f} GB{marker}")

        if not devices:
            print("✗ No USB devices found")
            sys.exit(1)

        selected = next((d for d in devices if d["device"] == self.device), None)
        if not selected:
            print(f"✗ Device not found: {self.device}")
            sys.exit(1)

        print(f"\n⚠️  WARNING: This will erase all data on {self.device} ({selected['size_gb']:.1f} GB)")
        response = input("Continue? [yes/NO]: ")

        if response.lower() != "yes":
            print("Aborted.")
            sys.exit(0)

        return True

    def wipe_device(self):
        """Securely wipe device."""
        print("\n🗑️  Wiping device...")
        self.run_cmd(["wipefs", "-a", self.device], needs_sudo=True)

    def create_partitions(self, boot_size_mb: int = 256, iso_format: str = "ext4"):
        """Create partition table and partitions."""
        print("\n📂 Creating partition layout...")
        print(f"   Partition 1 (Boot):  {boot_size_mb} MB")
        print("   Partition 2 (ISOs):  Remaining space")

        # MBR partition table
        self.run_cmd(["parted", "-s", self.device, "mklabel", "msdos"], needs_sudo=True)

        # Align partition 1 to 1MiB
        start = "1MiB"
        end = f"{boot_size_mb + 1}MiB"

        # Boot partition (ext4)
        self.run_cmd(
            ["parted", "-s", self.device, "mkpart", "primary", "ext4", start, end],
            needs_sudo=True,
        )
        self.run_cmd(["parted", "-s", self.device, "set", "1", "boot", "on"], needs_sudo=True)

        # ISO partition (ext4 or exfat)
        self.run_cmd(
            ["parted", "-s", self.device, "mkpart", "primary", iso_format, end, "100%"],
            needs_sudo=True,
        )

    def format_partitions(self, iso_format: str = "ext4"):
        """Format partitions."""
        print("\n💾 Formatting partitions...")

        if not self.dry_run:
            time.sleep(1)  # Wait for partitions to appear

        print(f"   {self.boot_partition} → ext4 (BOOT)")
        self.run_cmd(["mkfs.ext4", "-F", "-L", "BOOT", self.boot_partition], needs_sudo=True)

        print(f"   {self.iso_partition} → {iso_format} (ISOs)")
        if iso_format == "ext4":
            self.run_cmd(
                ["mkfs.ext4", "-F", "-L", "ISOs", self.iso_partition], needs_sudo=True
            )
        else:  # exfat
            self.run_cmd(
                ["mkfs.exfat", "-n", "ISOs", self.iso_partition], needs_sudo=True
            )


class GRUBInstaller:
    """Manages GRUB2 installation and configuration."""

    def __init__(self, device: str, mount_point: str, dry_run: bool = True):
        self.device = device
        self.mount_point = Path(mount_point)
        self.boot_mount = self.mount_point / "boot"
        self.iso_mount = self.mount_point / "iso"
        self.dry_run = dry_run
        self.boot_partition = f"{device}1"
        self.iso_partition = f"{device}2"

    def run_cmd(self, cmd: List[str], needs_sudo: bool = False) -> Tuple[int, str]:
        """Execute command."""
        if needs_sudo and os.geteuid() != 0:
            cmd = ["sudo"] + cmd

        cmd_str = " ".join(cmd)
        print(f"→ {cmd_str}")

        if self.dry_run:
            print("  [DRY-RUN: skipped]")
            return 0, ""

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            return result.returncode, result.stdout
        except subprocess.CalledProcessError as e:
            print(f"✗ Command failed: {cmd_str}")
            print(f"  {e.stderr}")
            raise

    def mount_partitions(self):
        """Mount USB partitions."""
        print("\n🔗 Mounting partitions...")

        if self.dry_run:
            print(f"   {self.boot_partition} → {self.boot_mount}")
            print(f"   {self.iso_partition} → {self.iso_mount}")
            return  # <--- RETURN EARLY IN DRY RUN

        self.mount_point.mkdir(parents=True, exist_ok=True)
        self.boot_mount.mkdir(exist_ok=True)
        self.iso_mount.mkdir(exist_ok=True)

        print(f"   {self.boot_partition} → {self.boot_mount}")
        self.run_cmd(["mount", str(self.boot_partition), str(self.boot_mount)], needs_sudo=True)

        print(f"   {self.iso_partition} → {self.iso_mount}")
        self.run_cmd(["mount", str(self.iso_partition), str(self.iso_mount)], needs_sudo=True)

    def install_grub(self):
        """Install GRUB2 bootloader."""
        print("\n🔧 Installing GRUB2...")

        if self.dry_run:
            print(f"   → grub-install --force --no-floppy --boot-directory={self.boot_mount} {self.device}")
            return  # <--- RETURN EARLY IN DRY RUN

        # Create boot directory structure
        grub_dir = self.boot_mount / "grub"
        grub_dir.mkdir(parents=True, exist_ok=True)

        # Install bootloader
        self.run_cmd(
            [
                "grub-install",
                "--force",
                "--no-floppy",
                f"--boot-directory={self.boot_mount}",
                self.device,
            ],
            needs_sudo=True,
        )

    def copy_isos(self, iso_dir: Path) -> Dict[str, float]:
        """Copy ISO files to USB."""
        print("\n📝 Copying ISO files...")

        iso_folder = self.iso_mount / "isos"
        isos = {}
        iso_files = sorted(iso_dir.glob("*.iso"))

        if not iso_files:
            print("⚠️  No ISO files found")
            return isos

        for iso_file in iso_files:
            size_gb = iso_file.stat().st_size / (1024 ** 3)
            isos[iso_file.name] = size_gb
            print(f"   {iso_file.name:50} {size_gb:6.2f} GB")

            if not self.dry_run:
                iso_folder.mkdir(exist_ok=True)  # <--- MOVED INSIDE CHECK
                dst = iso_folder / iso_file.name
                shutil.copy2(iso_file, dst)

        return isos

    def generate_grub_config(self, isos: Dict[str, float]) -> str:
        """Generate grub.cfg for multiboot."""
        config = """# GRUB2 Multiboot Configuration
# Auto-generated for GRUB2 Multiboot USB Creator

set default=0
set timeout=10

### Boot Entries ###
"""

        for idx, iso_name in enumerate(sorted(isos.keys())):
            label = iso_name.replace(".iso", "").replace("_", " ")
            config += f'''
menuentry "{label}" {{
    echo "Loading {label}..."
    set isofile=/isos/{iso_name}
    loopback loop $isofile
    insmod gfxterm
    terminal_output gfxterm
    
    linux (loop)/casper/vmlinuz iso-scan/filename=$isofile boot=casper noeject noprompt splash --
    initrd (loop)/casper/initrd
}}
'''

        config += """
### System Utilities ###
menuentry "UEFI Firmware Settings" {
    fwsetup
}

menuentry "Reboot" {
    reboot
}

menuentry "Power Off" {
    halt
}
"""

        return config

    def write_grub_config(self, config: str):
        """Write grub.cfg file."""
        print("\n⚙️  Writing GRUB configuration...")

        grub_cfg = self.boot_mount / "grub" / "grub.cfg"
        print(f"   {grub_cfg}")

        if not self.dry_run:
            grub_cfg.write_text(config)
            os.chmod(grub_cfg, 0o644)

    def unmount_partitions(self):
        """Safely unmount USB."""
        print("\n🔌 Unmounting...")

        if self.dry_run:
            print(f"   {self.boot_mount}")
            print(f"   {self.iso_mount}")
            return  # <--- RETURN EARLY IN DRY RUN

        for mount in [self.boot_mount, self.iso_mount]:
            if mount.exists():
                print(f"   {mount}")
                subprocess.run(["sudo", "umount", str(mount)], capture_output=True)

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Create secure GRUB2 multiboot USB drives",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Interactive (recommended):
    python3 main.py --iso-dir ~/isos

  Automated dry-run:
    python3 main.py --iso-dir ~/isos --device /dev/sdb --auto-confirm

  Execute (no dry-run):
    python3 main.py --iso-dir ~/isos --device /dev/sdb --auto-confirm --no-dry-run
        """,
    )

    parser.add_argument("--iso-dir", "-i", required=True, help="Directory with ISO files")
    parser.add_argument("--device", "-d", help="USB device (e.g., /dev/sdb)")
    parser.add_argument("--mount-point", "-m", default="/mnt/usb", help="Mount point")
    parser.add_argument(
        "--boot-size-mb", "--boot-size",
        dest="boot_size_mb",
        type=int,
        default=256,
        help="Boot partition size in MB (default: 256).",
    )
    parser.add_argument(
        "--iso-format",
        choices=["ext4", "exfat"],
        default="ext4",
        help="ISO partition format",
    )
    parser.add_argument("--dry-run", action="store_true", default=True, help="Preview mode")
    parser.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false", help="Execute changes"
    )
    parser.add_argument("--auto-confirm", action="store_true", help="Skip confirmation prompts")

    args = parser.parse_args()

    if not args.dry_run and os.geteuid() != 0:
        print("✗ --no-dry-run requires root.")
        print("  Re-run as: sudo python3 main.py ...")
        sys.exit(1)

    # Verify ISO directory exists
    iso_dir = Path(args.iso_dir).expanduser()
    if not iso_dir.exists():
        print(f"✗ ISO directory not found: {iso_dir}")
        sys.exit(1)

    # Interactive device selection if needed
    device = args.device
    if not device:
        formatter = USBFormatter("/dev/null", dry_run=True)
        devices = formatter.list_disks()

        print("\n📋 Available USB devices:")
        for idx, dev in enumerate(devices, 1):
            print(f"   {idx}. {dev['device']:15} {dev['size_gb']:7.1f} GB")

        if not devices:
            print("✗ No USB devices detected")
            sys.exit(1)

        try:
            choice = input(f"\nSelect device [1-{len(devices)}]: ")
            device = devices[int(choice) - 1]["device"]
        except (ValueError, IndexError):
            print("✗ Invalid selection")
            sys.exit(1)

    # Run workflow
    print("\n" + "=" * 60)
    print("GRUB2 Multiboot USB Creator")
    print("=" * 60)
    print(f"ISO Directory:   {iso_dir}")
    print(f"USB Device:      {device}")
    print(f"Mount Point:     {args.mount_point}")
    print(f"Boot Size:       {args.boot_size_mb} MB")
    print(f"ISO Format:      {args.iso_format}")
    print(f"Dry-Run Mode:    {args.dry_run}")
    print("=" * 60)

    # Step 1: Format USB
    formatter = USBFormatter(device, dry_run=args.dry_run)

    if not args.auto_confirm:
        formatter.confirm_device()

    formatter.wipe_device()
    formatter.create_partitions(args.boot_size_mb, args.iso_format)
    formatter.format_partitions(args.iso_format)

    # Step 2: Install GRUB
    installer = GRUBInstaller(device, args.mount_point, dry_run=args.dry_run)
    installer.mount_partitions()
    installer.install_grub()

    # Step 3: Copy ISOs and configure GRUB
    isos = installer.copy_isos(iso_dir)
    config = installer.generate_grub_config(isos)
    installer.write_grub_config(config)

    # Step 4: Unmount
    installer.unmount_partitions()

    # Summary
    print("\n" + "=" * 60)
    if args.dry_run:
        print("✓ Dry-run complete (no changes made)")
        print("  Run with --no-dry-run to execute")
    else:
        print("✓ Multiboot USB ready!")
        print(f"  Boot from {device} to use GRUB2 menu")
    print("=" * 60)


if __name__ == "__main__":
    main()
