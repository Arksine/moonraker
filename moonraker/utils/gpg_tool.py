from pathlib import Path
import subprocess
import tempfile
import shutil

class GPGTool:
    def __init__(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="gpgv_tmp_"))
        self.keyring = self.tmpdir / "pubring.gpg"

    @staticmethod
    def get_moonraker_root() -> Path:
        return Path(__file__).resolve().parents[2]

    def build_keyring(self, key_file: Path):
        cmd = [
            "gpg",
            "--batch",
            "--yes",
            "--dearmor",
            "-o",
            str(self.keyring),
            str(key_file),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr)

    def verify(self, sig_file: Path, data_file: Path):
        cmd = [
            "gpgv",
            "--keyring",
            str(self.keyring),
            str(sig_file),
            str(data_file),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True)

        return res.returncode == 0, res.stderr
    
    def verify_with_keychain(self, owner: str, project_name: str,
                              sig_file: Path, data_file: Path) -> bool:
        key_file = (
            self.get_moonraker_root()
            / "keychain"
            / owner
            / f"{project_name}.asc"
        )

        if not key_file.exists():
            raise FileNotFoundError(f"Key not found: {key_file}")

        self.build_keyring(key_file)

        ok, _ = self.verify(sig_file, data_file)
        return True if ok else False
    
    def cleanup(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
