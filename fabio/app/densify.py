#!/usr/bin/env python
# coding: utf-8
#
#    Project: X-ray image reader
#             https://github.com/silx-kit/fabio
#
#
#    Copyright (C) European Synchrotron Radiation Facility, Grenoble, France
#
#    Principal author:       Jérôme Kieffer (Jerome.Kieffer@ESRF.eu)
#
#  Permission is hereby granted, free of charge, to any person
#  obtaining a copy of this software and associated documentation files
#  (the "Software"), to deal in the Software without restriction,
#  including without limitation the rights to use, copy, modify, merge,
#  publish, distribute, sublicense, and/or sell copies of the Software,
#  and to permit persons to whom the Software is furnished to do so,
#  subject to the following conditions:
#
#  The above copyright notice and this permission notice shall be
#  included in all copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
#  EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
#  OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
#  NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
#  HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
#  WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
#  FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
#  OTHER DEALINGS IN THE SOFTWARE.

"""Convert a sparse fileformat (Generated by sparsify-Bragg from pyFAI) to a dense 
stack of frames in Eiger, Lima ... images.
"""

__author__ = "Jerome Kieffer"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__licence__ = "MIT"
__date__ = "04/05/2021"
__status__ = "production"

FOOTER = """
"""

import logging
logging.basicConfig()
logger = logging.getLogger("densify")
import sys
import argparse
import os
import time
import multiprocessing.pool
import numpy
from .. import eigerimage, limaimage, sparseimage
from ..openimage import openimage as fabio_open
from .._version import version as fabio_version
from ..utils.cli import ProgressBar, expand_args

try:
    import hdf5plugin
except ImportError:
    pass

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ARGUMENT_FAILURE = 2


def parse_args():
    """Parse command line arguments and returns those arguments"""

    epilog = """return codes: 0 means a success. 1 means the conversion
                contains a failure, 2 means there was an error in the
                arguments"""

    parser = argparse.ArgumentParser(prog="densify",
                                     description=__doc__,
                                     epilog=epilog)
    parser.add_argument("IMAGE", nargs="*",
                        help="File with input images")
    parser.add_argument("-V", "--version", action='version', version=fabio_version,
                        help="output version and exit")
    parser.add_argument("-v", "--verbose", action='store_true', dest="verbose", default=False,
                        help="show information for each conversions")
    parser.add_argument("--debug", action='store_true', dest="debug", default=False,
                        help="show debug information")
    group = parser.add_argument_group("main arguments")
    group.add_argument("-l", "--list", action="store_true", dest="list", default=None,
                       help="show the list of available output formats and exit")
    group.add_argument("-o", "--output", default=None, type=str,
                       help="output filename, by default {baseame}_densify.h5")
    group.add_argument("-O", "--output-format", dest="format", default='lima', type=str,
                       help="output format among 'lima', 'eiger' ...")
    group.add_argument("-D", "--dummy", type=int, default=None,
                       help="Set masked values to this dummy value")

    group = parser.add_argument_group("optional behaviour arguments")
#     group.add_argument("-f", "--force", dest="force", action="store_true", default=False,
#                        help="if an existing destination file cannot be" +
#                        " opened, remove it and try again (this option" +
#                        " is ignored when the -n option is also used)")
#     group.add_argument("-n", "--no-clobber", dest="no_clobber", action="store_true", default=False,
#                        help="do not overwrite an existing file (this option" +
#                        " is ignored when the -i option is also used)")
#     group.add_argument("--remove-destination", dest="remove_destination", action="store_true", default=False,
#                        help="remove each existing destination file before" +
#                        " attempting to open it (contrast with --force)")
#     group.add_argument("-u", "--update", dest="update", action="store_true", default=False,
#                        help="copy only when the SOURCE file is newer" +
#                        " than the destination file or when the" +
#                        " destination file is missing")
#     group.add_argument("-i", "--interactive", dest="interactive", action="store_true", default=False,
#                        help="prompt before overwrite (overrides a previous -n" +
#                        " option)")
    group.add_argument("--dry-run", dest="dry_run", action="store_true", default=False,
                       help="do everything except modifying the file system")
    group.add_argument("-N", "--no-noise", action="store_false", dest="noisy", default=True,
                       help="Disable the noise reconstruction")
#     group = parser.add_argument_group("Image preprocessing (Important: applied in this order!)")
#     group.add_argument("--rotation", type=int, default=180,
#                        help="Rotate the initial image by this value in degrees. Must be a multiple of 90°. By default 180 deg (flip_up with origin=lower and flip_lr because the image is seen from the sample).")
#     group.add_argument("--transpose", default=False, action="store_true",
#                        help="Flip the x/y axis")
#     group.add_argument("--flip-ud", dest="flip_ud", default=False, action="store_true",
#                        help="Flip the image upside-down")
#     group.add_argument("--flip-lr", dest="flip_lr", default=False, action="store_true",
#                        help="Flip the image left-right")

    try:
        args = parser.parse_args()

        if args.debug:
            logger.setLevel(logging.DEBUG)

        if args.list:
            print("Supported output formats: LimaImage, EigerImage, soon NxMx")
            return EXIT_SUCCESS

        if len(args.IMAGE) == 0:
            raise argparse.ArgumentError(None, "No input file specified.")

        # the upper case IMAGE is used for the --help auto-documentation
        args.images = expand_args(args.IMAGE)
        args.images.sort()
        args.format = args.format.lower()
    except argparse.ArgumentError as e:
        logger.error(e.message)
        logger.debug("Backtrace", exc_info=True)
        return EXIT_ARGUMENT_FAILURE
    return args


class Converter:
    "Convert sparse format to dense HDF5 format"

    def __init__(self, args):
        self.args = args
        self.pb = ProgressBar("Decompression", 50, 50)
        sparseimage.SparseImage.NOISY = self.args.noisy

    def decompress_one(self, filename):
        "Decompress one input files"
        self.pb.update(0, "Read input data")
        t0 = time.perf_counter()
        sparse = fabio_open(filename)
        assert isinstance(sparse, sparseimage.SparseImage)
        t1 = time.perf_counter()

        self.pb.max_value = sparse.nframes
        if self.args.format.startswith("lima"):
            dest = limaimage.LimaImage()
        elif self.args.format.startswith("eiger"):
            dest = eigerimage.EigerImage()
        else:
            raise RuntimeError(f"Unsupported output format {self.args.format}")
        self.pb.update(1, "Create thread pool")
        pool = multiprocessing.pool.ThreadPool(multiprocessing.cpu_count())
        self.pb.update(1, "Populate thread pool")

        future_frames = {idx: pool.apply_async(sparse._generate_data, (idx,))
                         for idx in range(sparse.nframes)}
        pool.close()
        for idx, future_frame in future_frames.items():
            self.pb.update(idx, f"Decompress frame #{idx:04d}")
            dest.set_data(future_frame.get(), idx)
        pool.join()

        # dest.set_data
        t2 = time.perf_counter()
        output = self.args.output
        if self.args.format.startswith("eiger"):
            dest.dataset = [numpy.asarray(dest.dataset)]
        if self.args.output is None:
            output = os.path.splitext(filename)[0] + "_dense.h5"
        self.pb.update(self.pb.max_value, f"Save {output}")
        dest.save(output)
        t3 = time.perf_counter()
        self.pb.clear()
        print(f"Densify of {filename} --> {output} took:")
        print(f"Read input: {t1-t0:.3f}s")
        print(f"Decompress: {t2-t1:.3f}s")
        print(f"Write outp: {t3-t2:.3f}s")

    def decompress(self):
        "Decompress all input files"
        for filename in self.args.images:
            self.decompress_one(filename)


def main():
    args = parse_args()
    if args == EXIT_ARGUMENT_FAILURE:
        raise
    try:
        c = Converter(args)
        c.decompress()
    except Exception as err:
        logger.error(err.message)
        logger.debug("Backtrace", exc_info=True)
        return EXIT_FAILURE
    else:
        return EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
