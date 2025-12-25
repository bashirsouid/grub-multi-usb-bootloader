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
import urllib.request
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

    def check_existing_setup(self) -> bool:
        """Check if device looks like it was already set up by this tool."""
        # Simple heuristic: Check if partition 1 and 2 exist
        p1 = Path(self.boot_partition)
        p2 = Path(self.iso_partition)
        
        if p1.exists() and p2.exists():
            return True
        return False

    def confirm_device(self) -> str:
        """Display disks and confirm device selection. Returns 'wipe' or 'update'."""
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

        # Check for existing setup
        is_existing = self.check_existing_setup()
        
        if is_existing:
            print(f"\n⚠️  Existing partitions detected on {self.device}.")
            print("   [1] Update ISOs/Config only (Keep partitions)")
            print("   [2] Full Wipe & Reinstall (Erase everything)")
            choice = input("Select option [1/2]: ").strip()
            
            if choice == "1":
                return "update"
            elif choice == "2":
                print(f"\n⚠️  WARNING: This will erase all data on {self.device} ({selected['size_gb']:.1f} GB)")
                if input("Confirm full wipe? [yes/NO]: ").lower() == "yes":
                    return "wipe"
                sys.exit(0)
            else:
                print("Aborted.")
                sys.exit(0)
        else:
            print(f"\n⚠️  WARNING: This will erase all data on {self.device} ({selected['size_gb']:.1f} GB)")
            response = input("Continue? [yes/NO]: ")
            if response.lower() == "yes":
                return "wipe"
            sys.exit(0)

    def wipe_device(self):
        """Securely wipe device."""
        print("\n🗑️  Wiping device...")
        self.run_cmd(["wipefs", "-a", self.device], needs_sudo=True)

    def create_partitions(self, boot_size_mb: int = 256, iso_format: str = "ext4"):
        """Create partition table and partitions."""
        print("\n📂 Creating partition layout...")
        print(f"   Partition 1 (Boot):  {boot_size_mb} MB")
        print("   Partition 2 (ISOs):  Remaining space")

        self.run_cmd(["parted", "-s", self.device, "mklabel", "msdos"], needs_sudo=True)

        start = "1MiB"
        end = f"{boot_size_mb + 1}MiB"

        self.run_cmd(
            ["parted", "-s", self.device, "mkpart", "primary", "ext4", start, end],
            needs_sudo=True,
        )
        self.run_cmd(["parted", "-s", self.device, "set", "1", "boot", "on"], needs_sudo=True)

        self.run_cmd(
            ["parted", "-s", self.device, "mkpart", "primary", iso_format, end, "100%"],
            needs_sudo=True,
        )

    def format_partitions(self, iso_format: str = "ext4"):
        """Format partitions."""
        print("\n💾 Formatting partitions...")

        if not self.dry_run:
            time.sleep(1)

        print(f"   {self.boot_partition} → ext4 (BOOT)")
        self.run_cmd(["mkfs.ext4", "-F", "-L", "BOOT", self.boot_partition], needs_sudo=True)

        print(f"   {self.iso_partition} → {iso_format} (ISOs)")
        if iso_format == "ext4":
            self.run_cmd(
                ["mkfs.ext4", "-F", "-L", "ISOs", self.iso_partition], needs_sudo=True
            )
        else:
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
        print("\n🔗 Mounting partitions...")

        if self.dry_run:
            print(f"   {self.boot_partition} → {self.boot_mount}")
            print(f"   {self.iso_partition} → {self.iso_mount}")
            return

        self.mount_point.mkdir(parents=True, exist_ok=True)
        self.boot_mount.mkdir(exist_ok=True)
        self.iso_mount.mkdir(exist_ok=True)

        self.run_cmd(["mount", str(self.boot_partition), str(self.boot_mount)], needs_sudo=True)
        self.run_cmd(["mount", str(self.iso_partition), str(self.iso_mount)], needs_sudo=True)

    def install_grub(self):
        print("\n🔧 Installing GRUB2...")
        if self.dry_run:
            print(f"   → grub-install ... {self.device}")
            return

        grub_dir = self.boot_mount / "grub"
        grub_dir.mkdir(parents=True, exist_ok=True)

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

    def ensure_wimboot(self):
        """Download wimboot if needed."""
        wimboot_path = self.boot_mount / "grub" / "wimboot"
        if self.dry_run or wimboot_path.exists():
            return

        print("\n📥 Downloading wimboot (for Windows/PE support)...")
        url = "https://github.com/ipxe/wimboot/releases/latest/download/wimboot"
        try:
            urllib.request.urlretrieve(url, wimboot_path)
            print("   ✓ wimboot downloaded")
        except Exception as e:
            print(f"   ⚠️ Failed to download wimboot: {e}")

    def copy_isos(self, iso_dir: Optional[Path]) -> Dict[str, float]:
        print("\n📝 Syncing ISO files...")
        iso_folder = self.iso_mount / "isos"
        
        if not self.dry_run:
            iso_folder.mkdir(exist_ok=True)
            # Make accessible to all users
            subprocess.run(["sudo", "chmod", "777", str(iso_folder)], check=False)

        isos = {}
        
        # Scan what's already on the USB (in case of update mode)
        if not self.dry_run:
             for existing in iso_folder.glob("*.iso"):
                size_gb = existing.stat().st_size / (1024 ** 3)
                isos[existing.name] = size_gb

        if not iso_dir:
            print("   (No source directory - using existing files only)")
            return isos

        iso_files = sorted(iso_dir.glob("*.iso"))
        for iso_file in iso_files:
            size_gb = iso_file.stat().st_size / (1024 ** 3)
            isos[iso_file.name] = size_gb
            
            # Check if we need to copy
            dst = iso_folder / iso_file.name
            if self.dry_run:
                print(f"   + {iso_file.name:50} {size_gb:6.2f} GB")
                continue

            if dst.exists():
                if dst.stat().st_size == iso_file.stat().st_size:
                    print(f"   = {iso_file.name} (Up to date)")
                    continue
            
            print(f"   > Copying {iso_file.name}...")
            shutil.copy2(iso_file, dst)
            # Fix permissions on the file
            subprocess.run(["sudo", "chmod", "666", str(dst)], check=False)

        return isos

    def generate_grub_config(self, isos: Dict[str, float]) -> str:
        """Generate grub.cfg with robust distro detection."""
        
        # Check for Windows/Hiren's
        has_windows = any(x for x in isos.keys() if "win" in x.lower() or "hiren" in x.lower())
        if has_windows and not self.dry_run:
            self.ensure_wimboot()

        config = """# GRUB2 Multiboot Configuration
set default=0
set timeout=10
set pager=1

insmod part_msdos
insmod part_gpt
insmod ext2
insmod search_fs_uuid
insmod ntfs
insmod iso9660

if [ -e ($root)/boot/grub/wimboot ]; then
    set wimboot=($root)/boot/grub/wimboot
fi
"""

        for iso_name in sorted(isos.keys()):
            label = iso_name.replace(".iso", "").replace("_", " ")
            path = f"/isos/{iso_name}"
            lower = iso_name.lower()

            if "win" in lower or "hiren" in lower:
                config += f'''
menuentry "{label} (Windows/PE)" {{
    set isofile="{path}"
    loopback loop $isofile
    linux16 $wimboot
    initrd16 newc:bootmgr:(loop)/bootmgr newc:bcd:(loop)/boot/bcd newc:boot.sdi:(loop)/boot/boot.sdi newc:boot.wim:(loop)/sources/boot.wim
}}
'''
            elif "nixos" in lower:
                config += f'''
menuentry "{label}" {{
    set isofile="{path}"
    loopback loop $isofile
    linux (loop)/boot/bzImage init=/nix/store/eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee-nixos-system-*-*/init findiso=$isofile
    initrd (loop)/boot/initrd
}}
'''
            elif "tails" in lower:
                config += f'''
menuentry "{label}" {{
    set isofile="{path}"
    loopback loop $isofile
    linux (loop)/live/vmlinuz boot=live config findiso=$isofile live-media=removable apparmor=1 security=apparmor nopersistence noprompt timezone=Etc/UTC block.events_dfl_poll_msecs=1000 splash noautologin module=Tails
    initrd (loop)/live/initrd.img
}}
'''
            elif "arch" in lower:
                config += f'''
menuentry "{label}" {{
    set isofile="{path}"
    loopback loop $isofile
    probe -u $root --set=rootuuid
    linux (loop)/arch/boot/x86_64/vmlinuz-linux archisobasedir=arch archisolabel=ARCH_202X img_dev=/dev/disk/by-uuid/$rootuuid img_loop=$isofile earlymodules=loop
    initrd (loop)/arch/boot/intel-ucode.img (loop)/arch/boot/amd-ucode.img (loop)/arch/boot/x86_64/initramfs-linux.img
}}
'''
            elif "fedora" in lower or "rhel" in lower:
                config += f'''
menuentry "{label}" {{
    set isofile="{path}"
    loopback loop $isofile
    linux (loop)/images/pxeboot/vmlinuz iso-scan/filename=$isofile root=live:CDLABEL=Fedora-WS-Live-*-*-* ro rd.live.image quiet
    initrd (loop)/images/pxeboot/initrd.img
}}
'''
            else:
                config += f'''
menuentry "{label}" {{
    set isofile="{path}"
    loopback loop $isofile
    linux (loop)/casper/vmlinuz boot=casper iso-scan/filename=$isofile noeject noprompt splash --
    initrd (loop)/casper/initrd
}}
'''

        config += """
menuentry "UEFI Firmware Settings" { fwsetup }
menuentry "Reboot" { reboot }
menuentry "Power Off" { halt }
"""
        return config

    def write_grub_config(self, config: str):
        print("\n⚙️  Writing GRUB configuration...")
        grub_cfg = self.boot_mount / "grub" / "grub.cfg"
        if not self.dry_run:
            grub_cfg.write_text(config)
            os.chmod(grub_cfg, 0o644)

    def unmount_partitions(self):
        print("\n🔌 Unmounting...")
        if self.dry_run: return
        for mount in [self.boot_mount, self.iso_mount]:
            if mount.exists():
                subprocess.run(["sudo", "umount", str(mount)], capture_output=True)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Create secure GRUB2 multiboot USB drives",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument("--iso-dir", "-i", help="Directory with ISO files")
    parser.add_argument("--device", "-d", help="USB device (e.g., /dev/sdb)")
    parser.add_argument("--mount-point", "-m", default="/mnt/usb", help="Mount point")
    parser.add_argument("--boot-size-mb", type=int, default=256, help="Boot partition size in MB")
    parser.add_argument("--iso-format", choices=["ext4", "exfat"], default="ext4")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false")
    parser.add_argument("--auto-confirm", action="store_true")

    args = parser.parse_args()

    if not args.dry_run and os.geteuid() != 0:
        print("✗ --no-dry-run requires root.")
        print("  Re-run as: sudo python3 main.py ...")
        sys.exit(1)

    iso_dir = None
    if args.iso_dir:
        iso_dir = Path(args.iso_dir).expanduser()
        if not iso_dir.exists():
            print(f"✗ ISO directory not found: {iso_dir}")
            sys.exit(1)

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

    # Detect mode (Wipe or Update)
    formatter = USBFormatter(device, dry_run=args.dry_run)
    mode = "wipe"
    if not args.auto_confirm:
        mode = formatter.confirm_device()

    print("\n" + "=" * 60)
    print("GRUB2 Multiboot USB Creator")
    print("=" * 60)
    print(f"Mode:            {mode.upper()}")
    print(f"USB Device:      {device}")
    print(f"Mount Point:     {args.mount_point}")
    print(f"Dry-Run Mode:    {args.dry_run}")
    print("=" * 60)

    # Execute workflow based on mode
    installer = GRUBInstaller(device, args.mount_point, dry_run=args.dry_run)

    if mode == "wipe":
        formatter.wipe_device()
        formatter.create_partitions(args.boot_size_mb, args.iso_format)
        formatter.format_partitions(args.iso_format)
        installer.mount_partitions()
        installer.install_grub()
    else:
        # Update mode: just mount
        print("\n📂 Keeping existing partitions...")
        installer.mount_partitions()

    # Common steps (sync ISOs + Config)
    isos = installer.copy_isos(iso_dir)
    config = installer.generate_grub_config(isos)
    installer.write_grub_config(config)

    installer.unmount_partitions()

    print("\n" + "=" * 60)
    if args.dry_run:
        print("✓ Dry-run complete (no changes made)")
    else:
        print("✓ Multiboot USB ready!")
    print("=" * 60)

if __name__ == "__main__":
    main()
