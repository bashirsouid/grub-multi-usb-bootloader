#!/usr/bin/env python3
"""
GRUB2 Multiboot USB Creator

Creates/updates a 2-partition multiboot USB:
- Partition 1 (BOOT, ext4): GRUB files + grub.cfg
- Partition 2 (ISOs, ext4 or exfat): /isos/*.iso payload

Supports:
- Dry-run by default
- Optional --iso-dir (skip copying; still generates config from existing ISOs on USB)
- Interactive prompt for ISO directory if not specified
- Interactive prompt for Mode (Update vs Wipe) if existing partitions found
- Fixes ISO directory ownership so the invoking (sudo) user can manage files
- Windows/PE/Hiren's support via wimboot
- Auto-unmounts busy drives before wiping
- Unique mount points per device (supports multiple USBs)
"""

import os
import sys
import time
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Tuple

# -------------------------
# Helpers
# -------------------------

def _sudo_uid_gid() -> Tuple[int, int]:
    """Return the *invoking* user's uid/gid when running under sudo, else current uid/gid."""
    uid = int(os.environ.get("SUDO_UID", str(os.getuid())))
    gid = int(os.environ.get("SUDO_GID", str(os.getgid())))
    return uid, gid

def _run(cmd: List[str], *, dry_run: bool, needs_sudo: bool = False) -> Tuple[int, str]:
    if needs_sudo and os.geteuid() != 0:
        cmd = ["sudo"] + cmd

    cmd_str = " ".join(cmd)
    print(f"→ {cmd_str}")

    if dry_run:
        print("   [DRY-RUN: skipped]")
        return 0, ""

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return res.returncode, res.stdout
    except subprocess.CalledProcessError as e:
        print(f"✗ Command failed: {cmd_str}")
        if e.stderr:
            print(e.stderr.strip())
        raise
    except FileNotFoundError:
        print(f"✗ Command not found: {cmd[0]}")
        sys.exit(1)

def _blkid_value(dev: str, field: str) -> str:
    """
    Read a single blkid value (LABEL/TYPE/UUID).
    Returns "" if not found.
    """
    try:
        res = subprocess.run(
            ["blkid", "-o", "value", "-s", field, dev],
            capture_output=True,
            text=True,
            check=False,
        )
        return (res.stdout or "").strip()
    except FileNotFoundError:
        return ""

def _is_mounted(path: Path) -> bool:
    try:
        res = subprocess.run(["mountpoint", "-q", str(path)])
        return res.returncode == 0
    except FileNotFoundError:
        # fallback: best effort
        return False


# -------------------------
# USB formatter
# -------------------------

class USBFormatter:
    """Partition/format operations + basic detection."""

    def __init__(self, device: str, dry_run: bool = True):
        self.device = device
        self.dry_run = dry_run
        
        # Handle NVMe/MMC naming (e.g., /dev/mmcblk0 -> /dev/mmcblk0p1)
        if device[-1].isdigit():
            self.boot_partition = f"{device}p1"
            self.iso_partition = f"{device}p2"
        else:
            self.boot_partition = f"{device}1"
            self.iso_partition = f"{device}2"

    def list_disks(self) -> List[Dict]:
        """List all block devices with sizes."""
        try:
            # We run this even in dry_run because we need to see devices to select one
            result = subprocess.run(
                ["lsblk", "-bdno", "NAME,SIZE,TYPE"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("✗ Failed to detect disks (need lsblk)")
            sys.exit(1)

        devices = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            name, size, typ = parts[:3]

            if typ != "disk":
                continue

            size_gb = int(size) / (1024 ** 3)
            devices.append({"device": f"/dev/{name}", "size_gb": size_gb})

        return devices

    def device_has_layout(self) -> bool:
        """
        Detect 'already set up' layout using labels (preferred) or device nodes (fallback).
        """
        if not Path(self.boot_partition).exists() or not Path(self.iso_partition).exists():
            return False
        
        boot_label = _blkid_value(self.boot_partition, "LABEL")
        iso_label = _blkid_value(self.iso_partition, "LABEL")
        
        if boot_label == "BOOT" and iso_label == "ISOs":
            return True
        
        # fallback heuristic: partitions exist
        return True

    def confirm_wipe(self) -> None:
        devices = self.list_disks()
        selected = next((d for d in devices if d["device"] == self.device), None)
        
        if not selected:
            print(f"✗ Device not found: {self.device}")
            sys.exit(1)

        print(f"\n⚠️  WARNING: This will erase all data on {self.device} ({selected['size_gb']:.1f} GB)")
        response = input("Type 'yes' to continue: ").strip().lower()
        if response != "yes":
            print("Aborted.")
            sys.exit(0)

    def wipe_device(self) -> None:
        print("\n🔌 Unmounting all partitions on device...")
        if not self.dry_run:
            # Force unmount all partitions (e.g. sdb1, sdb2) to prevent "Device busy"
            # We use shell=True to allow wildcards like /dev/sdb*
            try:
                subprocess.run(f"umount {self.device}* 2>/dev/null", shell=True)
                time.sleep(1) # Give the kernel a moment to release
            except Exception:
                pass # explicit unmount might fail if not mounted, which is fine

        print("\n🗑️  Wiping device...")
        _run(["wipefs", "-a", self.device], dry_run=self.dry_run, needs_sudo=True)

    def create_partitions(self, boot_size_mb: int = 256, iso_format: str = "ext4") -> None:
        print("\n📂 Creating partition layout...")
        print(f"   Partition 1 (Boot): {boot_size_mb} MB")
        print("   Partition 2 (ISOs): Remaining space")

        _run(["parted", "-s", self.device, "mklabel", "msdos"], dry_run=self.dry_run, needs_sudo=True)
        
        # Align partition 1 at 1MiB to preserve embedding area.
        start = "1MiB"
        end = f"{boot_size_mb + 1}MiB"  # start is 1MiB, so +1MiB keeps size exact-ish

        _run(
            ["parted", "-s", self.device, "mkpart", "primary", "ext4", start, end],
            dry_run=self.dry_run,
            needs_sudo=True,
        )
        _run(["parted", "-s", self.device, "set", "1", "boot", "on"], dry_run=self.dry_run, needs_sudo=True)
        
        _run(
            ["parted", "-s", self.device, "mkpart", "primary", iso_format, end, "100%"],
            dry_run=self.dry_run,
            needs_sudo=True,
        )

    def format_partitions(self, iso_format: str = "ext4") -> None:
        print("\n💾 Formatting partitions...")
        if not self.dry_run:
            time.sleep(1) # wait for kernel to reread partition table

        print(f"   {self.boot_partition} → ext4 (BOOT)")
        _run(["mkfs.ext4", "-F", "-L", "BOOT", self.boot_partition], dry_run=self.dry_run, needs_sudo=True)

        print(f"   {self.iso_partition} → {iso_format} (ISOs)")
        if iso_format == "ext4":
            _run(["mkfs.ext4", "-F", "-L", "ISOs", self.iso_partition], dry_run=self.dry_run, needs_sudo=True)
        else:
            _run(["mkfs.exfat", "-n", "ISOs", self.iso_partition], dry_run=self.dry_run, needs_sudo=True)


# -------------------------
# GRUB installer + ISO sync
# -------------------------

class GRUBInstaller:
    def __init__(self, device: str, mount_point: str, dry_run: bool = True, iso_perms: str = "sudo-user"):
        self.device = device
        self.mount_point = Path(mount_point)
        self.boot_mount = self.mount_point / "boot"
        self.iso_mount = self.mount_point / "iso"
        self.dry_run = dry_run
        self.iso_perms = iso_perms

        # Handle NVMe/MMC naming (e.g., /dev/mmcblk0 -> /dev/mmcblk0p1)
        if device[-1].isdigit():
            self.boot_partition = f"{device}p1"
            self.iso_partition = f"{device}p2"
        else:
            self.boot_partition = f"{device}1"
            self.iso_partition = f"{device}2"

    def mount_partitions(self) -> None:
        print("\n🔗 Mounting partitions...")
        if self.dry_run:
            print(f"   {self.boot_partition} → {self.boot_mount}")
            print(f"   {self.iso_partition} → {self.iso_mount}")
            return

        self.mount_point.mkdir(parents=True, exist_ok=True)
        self.boot_mount.mkdir(exist_ok=True)
        self.iso_mount.mkdir(exist_ok=True)

        # Boot partition (ext4)
        if not _is_mounted(self.boot_mount):
            _run(["mount", self.boot_partition, str(self.boot_mount)], dry_run=self.dry_run, needs_sudo=True)

        # ISO partition (ext4/exfat)
        if not _is_mounted(self.iso_mount):
            fstype = _blkid_value(self.iso_partition, "TYPE")
            
            # For exfat-like FS, ownership is controlled by mount options.
            if fstype in {"exfat", "vfat", "fat", "fat32"} and self.iso_perms == "sudo-user":
                uid, gid = _sudo_uid_gid()
                opts = f"uid={uid},gid={gid},umask=022"
                _run(["mount", "-o", opts, self.iso_partition, str(self.iso_mount)],
                     dry_run=self.dry_run, needs_sudo=True)
            else:
                _run(["mount", self.iso_partition, str(self.iso_mount)], dry_run=self.dry_run, needs_sudo=True)

    def unmount_partitions(self) -> None:
        print("\n🔌 Unmounting...")
        if self.dry_run:
            print(f"   {self.boot_mount}")
            print(f"   {self.iso_mount}")
            return

        for m in (self.iso_mount, self.boot_mount):
            if _is_mounted(m):
                _run(["umount", str(m)], dry_run=self.dry_run, needs_sudo=True)

    def install_grub(self) -> None:
        print("\n🔧 Installing GRUB2...")
        if self.dry_run:
            print(f"   grub-install --force --no-floppy --boot-directory={self.boot_mount} {self.device}")
            return

        grub_dir = self.boot_mount / "grub"
        grub_dir.mkdir(parents=True, exist_ok=True)
        
        _run(
            [
                "grub-install",
                "--force",
                "--no-floppy",
                f"--boot-directory={self.boot_mount}",
                self.device,
            ],
            dry_run=self.dry_run,
            needs_sudo=True,
        )

    def _apply_iso_permissions(self, path: Path) -> None:
        if self.dry_run:
            return
        
        if self.iso_perms == "root":
            # leave as-is
            return
        
        if self.iso_perms == "world-writable":
            _run(["chmod", "-R", "a+rwX", str(path)], dry_run=self.dry_run, needs_sudo=True)
            return

        # default: sudo-user
        uid, gid = _sudo_uid_gid()
        _run(["chown", "-R", f"{uid}:{gid}", str(path)], dry_run=self.dry_run, needs_sudo=True)
        _run(["chmod", "755", str(path)], dry_run=self.dry_run, needs_sudo=True)
        
        # Files under it: readable by all, writable by owner.
        for p in path.rglob("*"):
            if p.is_dir():
                _run(["chmod", "755", str(p)], dry_run=self.dry_run, needs_sudo=True)
            else:
                _run(["chmod", "644", str(p)], dry_run=self.dry_run, needs_sudo=True)

    def ensure_wimboot(self, *, allow_download: bool) -> None:
        """
        Ensure wimboot exists in BOOT partition.
        If allow_download=False, only warns if missing.
        """
        wimboot_path = self.boot_mount / "grub" / "wimboot"
        
        if wimboot_path.exists():
            return
        
        if not allow_download:
            print("⚠️  wimboot missing; Windows/Hiren's entries may not boot.")
            print(f"    Expected: {wimboot_path}")
            return
            
        print("\n📥 Downloading wimboot (for Windows/PE support)...")
        if self.dry_run:
            print(f"   curl -L -o {wimboot_path} https://github.com/ipxe/wimboot/releases/latest/download/wimboot")
            return
            
        url = "https://github.com/ipxe/wimboot/releases/latest/download/wimboot"
        try:
            wimboot_path.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(url, wimboot_path)
            _run(["chmod", "644", str(wimboot_path)], dry_run=self.dry_run, needs_sudo=True)
            print("   ✓ wimboot downloaded")
        except Exception as e:
            print(f"   ⚠️  Failed to download wimboot: {e}")
            print("       Windows/Hiren's entries may not boot.")

    def scan_existing_isos(self) -> Dict[str, float]:
        """Scan ISOs already on the USB (iso partition)."""
        iso_folder = self.iso_mount / "isos"
        isos: Dict[str, float] = {}
        
        if not iso_folder.exists():
            return isos
            
        for p in sorted(iso_folder.glob("*.iso")):
            try:
                isos[p.name] = p.stat().st_size / (1024 ** 3)
            except FileNotFoundError:
                continue
        return isos

    def sync_isos(self, iso_dir: Optional[Path]) -> Dict[str, float]:
        """
        Ensure /isos exists, optionally copy *.iso from iso_dir, and return ISO list 
        (including anything already present).
        """
        print("\n🧩 Syncing ISO files...")
        iso_folder = self.iso_mount / "isos"
        
        if not self.dry_run:
            iso_folder.mkdir(exist_ok=True)
            
        # Start with what's already there (important for update mode and for --iso-dir omitted).
        isos = self.scan_existing_isos()

        if not iso_dir:
            print("   (No --iso-dir specified; using ISOs already present on the USB, if any.)")
            if not self.dry_run:
                self._apply_iso_permissions(iso_folder)
            return isos
            
        src_files = sorted(iso_dir.glob("*.iso"))
        if not src_files:
            print("   (No *.iso found in --iso-dir; leaving existing USB ISOs as-is.)")
            if not self.dry_run:
                self._apply_iso_permissions(iso_folder)
            return isos
            
        for src in src_files:
            dst = iso_folder / src.name
            size_gb = src.stat().st_size / (1024 ** 3)
            
            if self.dry_run:
                print(f"   + {src.name:50} {size_gb:6.2f} GB")
                isos[src.name] = size_gb
                continue
                
            # Copy only if missing or size differs (simple “sync” behavior).
            if dst.exists() and dst.stat().st_size == src.stat().st_size:
                print(f"   = {src.name} (up-to-date)")
            else:
                print(f"   > Copying {src.name}...")
                shutil.copy2(src, dst)
            
            isos[src.name] = size_gb

        if not self.dry_run:
            self._apply_iso_permissions(iso_folder)

        return isos

    def generate_grub_config(self, isos: Dict[str, float], *, allow_wimboot_download: bool) -> str:
        """
        Generate grub.cfg with robust distro detection and loopback cleanup.
        """
        # If Windows/Hiren detected, ensure wimboot is present (or warn).
        has_windows = any(
            x for x in isos.keys() 
            for term in ["win", "hiren", "hbcd", "pe", "gandalf"] 
            if term in x.lower()
        )
        if has_windows:
            self.ensure_wimboot(allow_download=allow_wimboot_download)

        cfg = """# GRUB2 Multiboot Configuration
# Auto-generated

set default=0
set timeout=10
set pager=1

insmod part_msdos
insmod part_gpt
insmod ext2
insmod iso9660
insmod ntfs
insmod loopback

# Quietly find partitions using hints to avoid "device not found" noise
search --no-floppy --label BOOT --set=bootpart --hint ($root)
search --no-floppy --label ISOs --set=isopart --hint ($root)

# Fallback if label search fails
if [ -z "$isopart" ]; then
    set isopart=($root)
fi

# Get UUID of ISO partition for Linux kernels
probe -u $isopart --set=isouuid

# wimboot (if installed)
if [ -e ($bootpart)/grub/wimboot ]; then
  set wimboot=($bootpart)/grub/wimboot
fi

### ISO Entries ###
"""

        def _menuentry(label: str, body: str) -> str:
            return f'\nmenuentry "{label}" {{\n{body}\n}}\n'

        for iso_name in sorted(isos.keys()):
            label = iso_name.replace(".iso", "").replace("_", " ").strip()
            isofile = f"/isos/{iso_name}"
            low = iso_name.lower()

            # ---------------------------------------------------------
            # 1. Windows / WinPE / Hiren's / Gandalf
            # ---------------------------------------------------------
            if any(x in low for x in ["hiren", "hbcd", "gandalf", "win10", "win11", "windows", "winpe", "pe_x64"]):
                body = f"""  set isofile="{isofile}"
  loopback --delete loop
  loopback loop ($isopart)$isofile
  
  if [ -z "$wimboot" ]; then
    echo "wimboot not found at ($bootpart)/grub/wimboot"
    echo "Install it or rerun with --download-wimboot"
    sleep 5
  else
    # SHOTGUN MAPPING: Map every possible case/name to ensure wimboot finds it.
    linux16 $wimboot
    initrd16 \\
      newc:bootmgr:(loop)/bootmgr \\
      newc:bootmgr:(loop)/BOOTMGR \\
      newc:bootmgr.exe:(loop)/bootmgr.exe \\
      newc:bootmgr.exe:(loop)/BOOTMGR.EXE \\
      newc:bcd:(loop)/boot/bcd \\
      newc:bcd:(loop)/BOOT/BCD \\
      newc:boot.sdi:(loop)/boot/boot.sdi \\
      newc:boot.sdi:(loop)/BOOT/BOOT.SDI \\
      newc:boot.wim:(loop)/sources/boot.wim \\
      newc:boot.wim:(loop)/SOURCES/BOOT.WIM
  fi
"""
                cfg += _menuentry(f"{label} (Windows/PE)", body)
                continue

            # ---------------------------------------------------------
            # 2. NixOS (Fixed: Use internal loopback.cfg)
            # ---------------------------------------------------------
            if "nixos" in low:
                body = f"""  set isofile="{isofile}"
  loopback --delete loop
  loopback loop ($isopart)$isofile
  
  # NixOS ISOs ship with a specialized GRUB config for loopback booting.
  # We just chainload it.
  if [ -e (loop)/boot/grub/loopback.cfg ]; then
      configfile (loop)/boot/grub/loopback.cfg
  else
      echo "No loopback.cfg found in NixOS ISO."
      sleep 5
  fi
"""
                cfg += _menuentry(label, body)
                continue

            # ---------------------------------------------------------
            # 3. Debian Installer (Netinst/DVD) - Preserved your fix
            # ---------------------------------------------------------
            if "debian" in low and "netinst" in low:
                body = f"""  set isofile="{isofile}"
  loopback --delete loop
  loopback loop ($isopart)$isofile
  linux (loop)/install.amd/vmlinuz vga=788 --- quiet
  initrd (loop)/install.amd/initrd.gz
"""
                cfg += _menuentry(label, body)
                continue

            # ---------------------------------------------------------
            # 4. Tails
            # ---------------------------------------------------------
            if "tails" in low:
                body = f"""  set isofile="{isofile}"
  loopback --delete loop
  loopback loop ($isopart)$isofile
  linux (loop)/live/vmlinuz boot=live config findiso=$isofile live-media=removable apparmor=1 security=apparmor nopersistence noprompt timezone=Etc/UTC block.events_dfl_poll_msecs=1000 splash noautologin module=Tails
  initrd (loop)/live/initrd.img
"""
                cfg += _menuentry(label, body)
                continue

            # ---------------------------------------------------------
            # 5. SystemRescue
            # ---------------------------------------------------------
            if "systemrescue" in low or "sysresccd" in low:
                body = f"""  set isofile="{isofile}"
  loopback --delete loop
  loopback loop ($isopart)$isofile
  
  if [ -e (loop)/sysresccd/boot/x86_64/vmlinuz ]; then
      if [ -n "$isouuid" ]; then
          set imgdev="/dev/disk/by-uuid/$isouuid"
      else
          set imgdev="$isopart" 
      fi
      linux (loop)/sysresccd/boot/x86_64/vmlinuz archisobasedir=sysresccd archisolabel=RESCUE* img_dev=$imgdev img_loop=$isofile earlymodules=loop
      initrd (loop)/sysresccd/boot/x86_64/sysresccd.img
  else
      linux (loop)/isolinux/rescue64 isoloop=$isofile
      initrd (loop)/isolinux/initram.igz
  fi
"""
                cfg += _menuentry(label, body)
                continue

            # ---------------------------------------------------------
            # 6. Debian-live family (Clonezilla, GParted, Kali, Ubuntu)
            # ---------------------------------------------------------
            if any(x in low for x in ["clonezilla", "gparted", "debian", "kali", "ubuntu", "pop", "mint"]):
                extra = ""
                if "clonezilla" in low:
                    extra = "union=overlay components quiet noswap"
                
                # Check for casper (Ubuntu/Mint) vs live (Debian/Kali)
                # We can do this dynamically in GRUB
                body = f"""  set isofile="{isofile}"
  loopback --delete loop
  loopback loop ($isopart)$isofile
  
  if [ -e (loop)/casper/vmlinuz ]; then
      linux (loop)/casper/vmlinuz boot=casper iso-scan/filename=$isofile noeject noprompt splash --
      initrd (loop)/casper/initrd
  elif [ -e (loop)/live/vmlinuz ]; then
      linux (loop)/live/vmlinuz boot=live findiso=$isofile {extra}
      initrd (loop)/live/initrd.img
  else
      echo "Could not find kernel in /casper or /live"
      sleep 5
  fi
"""
                cfg += _menuentry(label, body)
                continue

            # ---------------------------------------------------------
            # 7. Arch family
            # ---------------------------------------------------------
            if any(x in low for x in ["arch", "manjaro", "endeavouros", "endeavour"]):
                body = f"""  set isofile="{isofile}"
  loopback --delete loop
  loopback loop ($isopart)$isofile
  probe -u ($isopart) --set=isouuid
  linux (loop)/arch/boot/x86_64/vmlinuz-linux archisobasedir=arch img_dev=/dev/disk/by-uuid/$isouuid img_loop=$isofile earlymodules=loop
  initrd (loop)/arch/boot/x86_64/initramfs-linux.img
"""
                cfg += _menuentry(label, body)
                continue

            # ---------------------------------------------------------
            # 8. Fedora/RHEL
            # ---------------------------------------------------------
            if any(x in low for x in ["fedora", "rhel", "centos", "rocky", "alma"]):
                body = f"""  set isofile="{isofile}"
  loopback --delete loop
  loopback loop ($isopart)$isofile
  linux (loop)/images/pxeboot/vmlinuz iso-scan/filename=$isofile rd.live.image quiet
  initrd (loop)/images/pxeboot/initrd.img
"""
                cfg += _menuentry(label, body)
                continue

            # ---------------------------------------------------------
            # 9. Generic Fallback
            # ---------------------------------------------------------
            body = f"""  set isofile="{isofile}"
  loopback --delete loop
  loopback loop ($isopart)$isofile
  linux (loop)/boot/vmlinuz iso-scan/filename=$isofile quiet
  initrd (loop)/boot/initrd
"""
            cfg += _menuentry(label, body)

        cfg += """
### Utilities ###
if [ "$grub_platform" = "efi" ]; then
    menuentry "UEFI Firmware Settings" { fwsetup }
fi
menuentry "Reboot" { reboot }
menuentry "Power Off" { halt }
"""
        return cfg

    def write_grub_config(self, cfg: str) -> None:
        print("\n⚙️  Writing GRUB configuration...")
        grub_cfg = self.boot_mount / "grub" / "grub.cfg"
        print(f"   {grub_cfg}")
        
        if self.dry_run:
            return
            
        grub_cfg.parent.mkdir(parents=True, exist_ok=True)
        grub_cfg.write_text(cfg)
        _run(["chmod", "644", str(grub_cfg)], dry_run=self.dry_run, needs_sudo=True)


# -------------------------
# Main
# -------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(
        description="Create or update a GRUB2 multiboot USB (BOOT + ISOs partitions)."
    )
    parser.add_argument("--iso-dir", "-i", help="Directory containing ISO files (optional)")
    parser.add_argument("--device", "-d", help="USB device (e.g., /dev/sdb)")
    parser.add_argument("--mount-point", "-m", default="/mnt/usb", help="Mount point (default: /mnt/usb)")
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
        help="ISO partition format (default: ext4).",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "wipe", "update"],
        default="auto",
        help="auto: detect existing setup; wipe: force wipe; update: only sync ISOs/config.",
    )
    parser.add_argument(
        "--iso-perms",
        choices=["sudo-user", "root", "world-writable"],
        default="sudo-user",
        help="Permissions for /isos on the USB (default: sudo-user).",
    )
    parser.add_argument(
        "--download-wimboot",
        action="store_true",
        help="Allow downloading wimboot for Windows/Hiren's support (network access).",
    )
    parser.add_argument("--dry-run", action="store_true", default=True, help="Preview mode (default: enabled)")
    parser.add_argument("--no-dry-run", dest="dry_run", action="store_false", help="Execute changes (requires root)")
    parser.add_argument("--auto-confirm", action="store_true", help="Skip interactive confirmations")

    args = parser.parse_args()

    if not args.dry_run and os.geteuid() != 0:
        print("✗ --no-dry-run requires root.")
        print("  Re-run as: sudo python3 main.py ...")
        sys.exit(1)

    # --- 1. ISO Directory ---
    iso_dir: Optional[Path] = None
    if args.iso_dir:
        iso_dir = Path(args.iso_dir).expanduser()
        if not iso_dir.exists():
            print(f"✗ ISO directory not found: {iso_dir}")
            sys.exit(1)

    # --- 2. Device Selection ---
    device = args.device
    if not device:
        formatter_probe = USBFormatter("/dev/null", dry_run=True)
        devices = formatter_probe.list_disks()
        
        print("\n📋 Available USB devices:")
        for idx, dev in enumerate(devices, 1):
            print(f"   {idx}. {dev['device']:15} {dev['size_gb']:7.1f} GB")
        
        if not devices:
            print("✗ No USB devices detected")
            sys.exit(1)
            
        try:
            choice = int(input(f"\nSelect device [1-{len(devices)}]: ").strip())
            device = devices[choice - 1]["device"]
        except Exception:
            print("✗ Invalid selection")
            sys.exit(1)

    formatter = USBFormatter(device, dry_run=args.dry_run)
    already = formatter.device_has_layout()

    # --- Mode Resolution ---
    mode = args.mode
    if mode == "auto":
        if already:
            # If auto-confirm is ON, we assume 'update' is the safe desired action for automation.
            # If interactive, we ask.
            if args.auto_confirm:
                mode = "update"
            else:
                print(f"\n⚠️  Existing multiboot partitions detected on {device}.")
                print("   [u] Update: Sync ISOs & update menu (preserves existing data)")
                print("   [w] Wipe:   Erase everything and start fresh")
                ans = input("Select mode [u/w]: ").strip().lower()
                if ans == "w":
                    mode = "wipe"
                else:
                    mode = "update"
        else:
            mode = "wipe"

    # --- ISO Dir Prompt (New Feature) ---
    # If we are wiping, or if updating but no ISO source provided, give user a chance to paste a path.
    # This helps pre-seed the drive without needing command line args.
    if not args.iso_dir and not args.auto_confirm and not args.dry_run:
        print("\n💿 Optional: Path to ISO directory to pre-seed?")
        p = input("   Enter directory (or press Enter to skip): ").strip()
        if p:
            pot_path = Path(p).expanduser()
            if pot_path.exists() and pot_path.is_dir():
                iso_dir = pot_path
            else:
                print(f"   (Path invalid or not found, skipping: {p})")

    print("\n" + "=" * 60)
    print("GRUB2 Multiboot USB Creator")
    print("=" * 60)
    print(f"USB Device:      {device}")
    print(f"Mode:            {mode.upper()}")
    print(f"ISO Directory:   {iso_dir if iso_dir else '(none)'}")
    print(f"Mount Point:     {args.mount_point}")
    print(f"Boot Size:       {args.boot_size_mb} MB")
    print(f"ISO Format:      {args.iso_format}")
    print(f"ISO Perms:       {args.iso_perms}")
    print(f"Dry-Run Mode:    {args.dry_run}")
    print("=" * 60)

    if args.dry_run and not args.auto_confirm:
        input("\nPress Enter to continue (dry-run)...")

    # Ensure unique mount point per device to avoid conflicts (e.g. /mnt/usb-sdb)
    if args.mount_point == "/mnt/usb":
        args.mount_point = f"/mnt/usb-{Path(device).name}"

    installer = GRUBInstaller(
        device=device,
        mount_point=args.mount_point,
        dry_run=args.dry_run,
        iso_perms=args.iso_perms,
    )

    try:
        if mode == "wipe":
            if not args.auto_confirm:
                formatter.confirm_wipe()
            
            formatter.wipe_device()
            formatter.create_partitions(args.boot_size_mb, args.iso_format)
            formatter.format_partitions(args.iso_format)
            
            # Mount and Install GRUB
            installer.mount_partitions()
            installer.install_grub()
            
            # Sync ISOs (empty or from dir)
            isos = installer.sync_isos(iso_dir)
            
            # Generate Config
            cfg = installer.generate_grub_config(isos, allow_wimboot_download=args.download_wimboot)
            installer.write_grub_config(cfg)
            
        else:
            # UPDATE mode
            installer.mount_partitions()
            
            # Sync ISOs
            isos = installer.sync_isos(iso_dir)
            
            # Regenerate Config
            cfg = installer.generate_grub_config(isos, allow_wimboot_download=args.download_wimboot)
            installer.write_grub_config(cfg)
            
            # Re-install GRUB binary just in case
            installer.install_grub()

    finally:
        installer.unmount_partitions()

    print("\n" + "=" * 60)
    if args.dry_run:
        print("✓ Dry-run complete (no changes made).")
        print("  Re-run with --no-dry-run to execute.")
    else:
        print("✓ Multiboot USB ready.")
    print("=" * 60)


if __name__ == "__main__":
    main()
