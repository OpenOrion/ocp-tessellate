from __future__ import print_function
from setuptools import setup, find_packages
from glob import glob
import os
import wheel.bdist_wheel
import setup
import zipfile
import platform
import shutil
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
is_repo = os.path.exists(os.path.join(here, ".git"))

class bdist_wheel_repaired(wheel.bdist_wheel.bdist_wheel):
    """bdist_wheel followed by auditwheel-repair"""

    def run(self):
        super().run()
        dist_files = self.distribution.dist_files

        # Exactly one wheel has been created in `self.dist_dir` and
        # recorded in `dist_files`
        [(_, _, bad_whl)] = dist_files
        assert os.path.dirname(bad_whl) == self.dist_dir
        with zipfile.ZipFile(bad_whl) as f:
            bad_whl_files = set(zi.filename for zi in f.infolist() if not zi.is_dir())

        # Conda libraries depend on their location in $conda_prefix because
        # relative RPATHs are used find libraries elsewhere in $conda_prefix
        # (e.g. [$ORIGIN/../../..:$ORIGIN/../../../]).
        #
        # `auditwheel` works by expanding the wheel into a temporary
        # directory and computing the external shared libraries required.
        # But the relative RPATHs are broken, so this fails.  Thankfully,
        # RPATHs all resolve to $conda_prefix/lib, so we can set
        # LD_LIBRARY_PATH to allow `auditwheel` to find them.
        lib_path = os.path.join(conda_prefix, "lib")

        # Do the repair, placing the repaired wheel into out_dir.
        out_dir = os.path.join(self.dist_dir, "repaired")
        system = platform.system()
        if system == "Linux":
            repair_wheel_linux(lib_path, bad_whl, out_dir)
        elif system == "Darwin":
            repair_wheel_macos(lib_path, bad_whl, out_dir)
        elif system == "Windows":
            repair_wheel_windows(lib_path, bad_whl, out_dir)
        else:
            raise Exception(f"unsupported system {system!r}")

        # Add licenses of bundled libraries
        [repaired_whl] = glob.glob(os.path.join(out_dir, "*.whl"))

        # Exactly one whl is expected in the dist dir, so delete the
        # bad wheel and move the repaired wheel in.
        os.unlink(bad_whl)
        new_whl = os.path.join(self.dist_dir, os.path.basename(repaired_whl))
        shutil.move(repaired_whl, new_whl)
        os.rmdir(out_dir)
        dist_files[0] = dist_files[0][:-1] + (new_whl,)


def repair_wheel_linux(lib_path, whl, out_dir):

    if os.uname()[4] == "aarch64":
        plat = "manylinux_2_35_aarch64"
    else:
        plat = "manylinux_2_35_x86_64"

    args = [
        "env",
        f"LD_LIBRARY_PATH={lib_path}",
        sys.executable,
        "-m",
        "auditwheel",
        "--verbose",
        "repair",
        f"--plat={plat}",
        f"--wheel-dir={out_dir}",
        whl,
    ]
    subprocess.check_call(args)


def repair_wheel_macos(lib_path, whl, out_dir):

    args = [
        "env",
        f"DYLD_LIBRARY_PATH={lib_path}",
        sys.executable,
        "-m",
        "delocate.cmd.delocate_listdeps",
        whl,
    ]
    subprocess.check_call(args)

    # Overwrites the wheel in-place by default
    args = [
        "env",
        f"DYLD_LIBRARY_PATH={lib_path}",
        sys.executable,
        "-m",
        "delocate.cmd.delocate_wheel",
        f"--wheel-dir={out_dir}",
        whl,
    ]
    subprocess.check_call(args)


def repair_wheel_windows(lib_path, whl, out_dir):
    args = [sys.executable, "-m", "delvewheel", "show", whl]
    subprocess.check_call(args)
    args = [
        sys.executable,
        "-m",
        "delvewheel",
        "repair",
        "-vv",
        "--no-mangle-all",
        f"--wheel-dir={out_dir}",
        whl,
    ]
    subprocess.check_call(args)

from distutils import log

log.set_verbosity(log.DEBUG)
log.info("setup.py entered")
log.info("$PATH=%s", os.environ["PATH"])

LONG_DESCRIPTION = (
    "Tessellate OCP (https://github.com/cadquery/OCP) objects to use with threejs"
)

setup_args = {
    "name": "ocp_tessellate_orion",
    "version": "0.0.1",
    "description": "Tessellate OCP objects",
    "long_description": LONG_DESCRIPTION,
    "include_package_data": True,
    "python_requires": ">=3.9",
    "install_requires": [
        "webcolors~=1.12",
        "numpy",
        "numpy-quaternion",
        "cachetools~=5.2.0",
        "imagesize",
    ],
    "extras_require": {
        "dev": {"twine", "bumpversion", "black", "pylint", "pyYaml"},
    },
    "packages": find_packages(),
    "zip_safe": False,
    "author": "Bernhard Walter",
    "author_email": "b_walter@arcor.de",
    "url": "https://github.com/bernhard-42/ocp-tessellate",
    "keywords": ["CAD", "cadquery"],
    "classifiers": [
        "Development Status :: 5 - Production/Stable",
        "Framework :: IPython",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Topic :: Multimedia :: Graphics",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
}

setup(**setup_args)
