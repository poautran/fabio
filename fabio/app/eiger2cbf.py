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
"""Portable image converter based on FabIO library
to export Eiger frames (including te one from LIMA)
to CBF and mimic the header from Dectris Pilatus.
"""

__author__ = "Jerome Kieffer"
__copyright__ = "European Synchrotron Radiation Facility, Grenoble, France"
__licence__ = "MIT"
__date__ = "15/09/2020"
__status__ = "production"

import logging
logging.basicConfig()

import sys
import os
import glob

import fabio
import numpy
import argparse
try:
    import hdf5plugin
except ImportError:
    pass

logger = logging.getLogger("eiger2cbf")
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ARGUMENT_FAILURE = 2

try:
    from scipy import constants
except ImportError:
    # Updated with scipy 1.4
    CONST_hc = 12.398419843320026 
else:
    CONST_hc = constants.c * constants.h / constants.e * 1e7


def expand_args(args):
    """
    Takes an argv and expand it (under Windows, cmd does not convert *.tif into
    a list of files.

    :param list args: list of files or wildcards
    :return: list of actual args
    """
    new = []
    for afile in args:
        if glob.has_magic(afile):
            new += glob.glob(afile)
        else:
            new.append(afile)
    return new

def convert_one(input_filename, options):
    """
    Convert a single file using options

    :param str input_filename: The input filename
    :param object options: List of options provided from the command line
    :rtype: bool
    :returns: True is the conversion succeeded
    """
    input_filename = os.path.abspath(input_filename)
    input_exists = os.path.exists(input_filename)

    if options.verbose:
        print("Converting file '%s'" % (input_filename))

    if not input_exists:
        logger.error("Input file '%s' do not exists. Conversion skipped.", input_filename)
        return False

    try:
        logger.debug("Load '%s'", input_filename)
        source = fabio.open(input_filename)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        logger.error("Loading input file '%s' failed cause: \"%s\". Conversion skipped.", input_filename, e.message)
        logger.debug("Backtrace", exc_info=True)
        return False
    
    pilatus_headers = fabio.cbfimage.PilatusHeader("")
    if isinstance(source, fabio.limaimage.LimaImage):
        #Populate the Pilatus header from the Lima
        entry_name = source.h5.attrs.get("default")
        if entry_name:
            entry = source.h5.get(entry_name)
            if entry:
                data_name = entry.attrs["default"]
                if data_name:
                    data_grp = entry.get(data_name)
                    if data_grp:
                        nxdetector = data_grp.parent
                        try:
                            detector = "%s, %s"%(nxdetector["detector_information/model"][()],
                                                 nxdetector["detector_information/name"][()])                        
                            pilatus_headers["Detector"] = detector
                        except Exception as e:
                            logger.warning("Error in searching for detector definition (%s): %s", type(e), e)
                        try:
                            pilatus_headers["Pixel_size"] = (nxdetector["detector_information/pixel_size/xsize"][()],
                                                             nxdetector["detector_information/pixel_size/ysize"][()])
                        except Exception as e:
                            logger.warning("Error in searching for pixel size (%s): %s", type(e), e)
                        try:
                            t1 = nxdetector["acquisition/exposure_time"][()]
                            t2 = nxdetector["acquisition/latency_time"][()]
                            pilatus_headers["Exposure_time"] = t1
                            pilatus_headers["Exposure_period"] = t1 + t2
                        except Exception as e:
                            logger.warning("Error in searching for exposure time (%s): %s", type(e), e)
    #Parse option for Pilatus headers
    if options.energy:
        pilatus_headers["Wavelength"] = CONST_hc/options.energy
    if options.distance:
        pilatus_headers["Detector_distance"] = options.distance
    if options.beam:
        pilatus_headers["Beam_xy"] = options.beam

        
    elif isinstance(source, fabio.eigerimage.EigerImage):
        raise NotImplementedError("Please implement Eiger detector data format parsing")
    
    for i, frame in enumerate(source):
        data = frame.data.astype("int32")
        mask = numpy.where(data == numpy.iinfo(frame.data.dtype).max)
        data[mask] = options.dummy
        converted = fabio.cbfimage.CbfImage(data=data)
        converted.pilatus_headers = pilatus_headers
        
        output_filename = options.output.format(index=i+options.offset)
        os.makedirs(os.path.dirname(output_filename), exist_ok=True)
        try:
            logger.debug("Write '%s'", output_filename)
            if not options.dry_run:
                converted.write(output_filename)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            logger.error("Saving output file '%s' failed cause: \"%s: %s\". Conversion skipped.", output_filename, type(e), e)
            logger.debug("Backtrace", exc_info=True)
            return False
    options.offset +=  source.nframes
    # a success
    return True


def convert_all(options):
    """Convert all the files from the command line.

    :param object options: List of options provided from the command line
    :rtype: bool
    :returns: True is the conversion succeeded
    """
    succeeded = True
    for filename in options.images:
        succeeded = succeeded and convert_one(filename, options)

    return succeeded



def main():

    epilog = """return codes: 0 means a success. 1 means the conversion
                contains a failure, 2 means there was an error in the
                arguments"""

    parser = argparse.ArgumentParser(prog="eiger2cbf",
                                     description=__doc__,
                                     epilog=epilog)
    parser.add_argument("IMAGE", nargs="*",
                        help="File with input images")
    parser.add_argument("-V", "--version", action='version', version=fabio.version,
                        help="output version and exit")
    parser.add_argument("-v", "--verbose", action='store_true', dest="verbose", default=False,
                        help="show information for each conversions")
    parser.add_argument("--debug", action='store_true', dest="debug", default=False,
                        help="show debug information")
    group = parser.add_argument_group("main arguments")
#     group.add_argument("-l", "--list", action="store_true", dest="list", default=None,
#                        help="show the list of available formats and exit")
    group.add_argument("-o", "--output", default='eiger2cbf/frame_{index:04d}.cbf', type=str,
                       help="output directory and filename template")
    group.add_argument("-O", "--offset", type=int, default=0,
                       help="index offset, CrysalisPro likes indexes to start at 1, Python starts at 0")
    group.add_argument("-D", "--dummy", type=int, default=-1,
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

    group = parser.add_argument_group("Experimental setup options")
    group.add_argument("-e", "--energy", type=float, default=None,
                       help="Energy of the incident beam in keV")
    group.add_argument("-d", "--distance", type=float, default=None,
                       help="Detector distance in meters")
    group.add_argument("-b", "--beam", nargs=2, type=float, default=None,
                       help="Direct beam in pixels x, y")

    try:
        args = parser.parse_args()

        if args.debug:
            logger.setLevel(logging.DEBUG)

#         if args.list:
#             print_supported_formats()
#             return

        if len(args.IMAGE) == 0:
            raise argparse.ArgumentError(None, "No input file specified.")

        # the upper case IMAGE is used for the --help auto-documentation
        args.images = expand_args(args.IMAGE)
        args.images.sort()
    except argparse.ArgumentError as e:
        logger.error(e.message)
        logger.debug("Backtrace", exc_info=True)
        return EXIT_ARGUMENT_FAILURE

    succeeded = convert_all(args)
    if not succeeded:
        print("Conversion or part of it failed. You can try with --debug to have more output information.")
        return EXIT_FAILURE

    return EXIT_SUCCESS


if __name__ == "__main__":
    result = main()
    sys.exit(result)
