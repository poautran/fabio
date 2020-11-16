# coding: utf-8
#
#    Project: X-ray image reader
#             https://github.com/silx-kit/fabio
#
#    Copyright 2020(C) European Synchrotron Radiation Facility, Grenoble, France
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

"""Template for FabIO image reader

This is a template for adding new file formats to FabIO

We hope it will be relatively easy to add new file formats to fabio in the
future.
The basic idea is the following:

2) readheader fills in a dictionary of "name":"value" pairs in self.header.
   No one expects to find anything much in there.

3) read fills in self.data with a numpy array holding the image.
   Some info are automatically exposed from data:
   * self.shape is the image dimensions,
   * self.dtype is the numpy.dtype of the data.

4) The member variables "_need_a_seek_to_read" and "_need_a_real_file" are there
   in case you have
   trouble with the transparent handling of bz2 and gz files.

5) Add your new module as an import into fabio.fabioformats.
   Your class will be registered automatically.

6) Fill out the magic numbers for your format in fabio.openimage if you know
   them (the characteristic first few bytes in the file)

7) Upload a testimage to the file release system and create a unittest testcase
   which opens an example of your new format, confirming the image has actually
   been read in successfully (eg check the mean, max, min and esd are all correct,
   perhaps orientation too)

8) Run pylint on your code and then please go clean it up. Have a go at mine
   while you are at it, before requesting a pull-request on github.

9) Bask in the warm glow of appreciation when someone unexpectedly learns they
   don't need to convert their data into another format

"""

__authors__ = ["Jerome Kieffer"]
__contact__ = "jerome.kieffer@esrf.fr"
__license__ = "MIT"
__copyright__ = "2020 ESRF"
__date__ = "16/11/2020"

import logging
logger = logging.getLogger(__name__)
import numpy
try:
    import h5py
except ImportError:
    h5py = None
else:
    try:
        import hdf5plugin
    except:
        pass
from .fabioutils import NotGoodReader
from .fabioimage import FabioImage, OrderedDict


class SparseImage(FabioImage):
    """FabIO image class for images compressed by sparsification of Bragg peaks 

    While the sparsification requires pyFAI and substential resources, re-densifying the data is easy.
    
    The program used for the sparsification is `sparsify-Bragg` from the pyFAI suite
    """

    DESCRIPTION = "spasify-Bragg"

    DEFAULT_EXTENSIONS = [".h5", ".hdf5", ".nxs"]

    def __init__(self, *arg, **kwargs):
        """
        Generic constructor
        """
        if not h5py:
            raise RuntimeError("fabio.SparseImage cannot be used without h5py. Please install h5py and restart")

        FabioImage.__init__(self, *arg, **kwargs)
        self.mask = None
        self._masked = None
        self.radius = None
        self.background_avg = None
        self.background_std = None
        self.frame_ptr = None
        self.index = None
        self.intensity = None
        self.dummy = None
        self.noise = False
        self.h5 = None

    def close(self):
        if self.h5 is not None:
            self.h5.close()
            self.dataset = None

    def _readheader(self, infile):
        """
        Read and decode the header of an image:

        :param infile: Opened python file (can be stringIO or bzipped file)
        """
        # list of header key to keep the order (when writing)
        self.header = self.check_header()

    def read(self, fname, frame=None):
        """
        Try to read image

        :param fname: name of the file
        :param frame: number of the frame
        """

        self.resetvals()
        self._readheader(fname)
        self.h5 = h5py.File(fname, mode="r")
        default_entry = self.h5.attrs.get("default")
        if default_entry is None or default_entry not in self.h5:
            raise NotGoodReader("HDF5 file does not contain any default entry.")
        entry = self.h5[default_entry]
        default_data = entry.attrs.get("default")
        if default_data is None or default_data not in entry:
            raise NotGoodReader("HDF5 file does not contain any default NXdata.")
        nx_data = entry[default_data]
        self.mask = nx_data["mask"][()]
        self.radius = nx_data["radius"][()]
        self.background_avg = nx_data["background_avg"]
#         self.background_std = default_data["background_std"]
        self.frame_ptr = nx_data["frame_ptr"][()]
        self.index = nx_data["index"]
        self.intensity = nx_data["intensity"]
        self.dummy = nx_data["dummy"][()]
        self._nframes = self.frame_ptr.shape[0] - 1

        if frame is not None:
            return self.getframe(int(frame))
        else:
            self.currentframe = 0
            self.data = self._generate_data(self.currentframe)
            self._shape = None
            return self

    def _generate_data(self, index=0):
        "Actually rebuilds the data for one frame"
        if self.h5 is None:
            logger.warning("Not data have been read from disk")
            return

        if self._masked is None:
            self._masked = numpy.where(numpy.logical_not(numpy.isfinite(self.mask)))
        bg = self.background_avg[index]
        dense = numpy.interp(self.mask, self.radius, bg)
        flat = dense.ravel()
        start, stop = self.frame_ptr[index:index + 2]
        flat[self.index[start:stop]] = self.intensity[start:stop]
        dtype = self.intensity.dtype
        if numpy.issubdtype(dtype, numpy.integer):
            dense = numpy.round(dense).astype(dtype)
        dense[self._masked] = self.dummy
        return dense

    def getframe(self, num):
        """ returns the frame numbered 'num' in the stack if applicable"""
        if self.nframes > 1:
            new_img = None
            if (num >= 0) and num < self.nframes:
                data = self._generate_data(num)
                new_img = self.__class__(data=data, header=self.header)
                new_img.mask = self.mask
                new_img.radius = self.radius
                new_img.background_avg = self.background_avg
#                 self.background_std = None
                new_img.frame_ptr = self.frame_ptr
                new_img.index = self.index
                new_img.intensity = self.intensity
                new_img.dummy = self.dummy
                new_img.noise = self.noise
                new_img.h5 = self.h5
                new_img._nframes = self.nframes
                new_img.currentframe = num
            else:
                raise IOError("getframe %s out of range [%s %s[" % (num, 0, self.nframes))
        else:
            new_img = FabioImage.getframe(self, num)
        return new_img

    def previous(self):
        """ returns the previous frame in the series as a fabioimage """
        return self.getframe(self.currentframe - 1)

    def next(self):
        """ returns the next frame in the series as a fabioimage """
        return self.getframe(self.currentframe + 1)


# This is not compatibility with old code:
sparseimage = SparseImage
