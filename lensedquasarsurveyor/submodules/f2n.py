#! /usr/bin/env python

import os
import numpy as np
from PIL import Image as im
from PIL import ImageOps as imop
from PIL import ImageDraw as imdw
import astropy.io.fits as ft


class f2nimage:
    def __init__(self, numpyarray=None, shape=(100, 100), fill=10000.0, verbose=True):
        """
        Give me a numpyarray, or give me a shape (in this case I build my own array and fill it with the value fill).

        We will call the first coordinate "x", the second "y".
        The origin (0,0) is to be seen as the pixel in the lower left corner. When converting the numpy array to a
        png file, we will take care of the orientation in that way.

        """

        self.verbose = verbose  # Should I print infos about what I do ?

        self.z1 = -1000.0

        self.z2 = 65000.0
        self.binfactor = 1
        self.upsamplefactor = 1

        self.pilimage = None  # For now, no PIL image. Some manipulations are to be done before.

        self.negative = False  # Will be set to true if choosen in makepilimage. Changes default colours.

        # The draw object is created when needed, see makedraw()
        self.draw = None

        # The fonts are loaded when needed, see loadtitlefont()
        self.titlefont = None
        self.labelfont = None
        self.infofont = None

        # Now the numpy array to hold the actual data.

        if np.any(numpyarray) is None:

            self.numpyarray = np.ones(shape, dtype=np.float32)*fill

        else:
            if not isinstance(numpyarray, np.ndarray):

                raise RuntimeError("Please give me numpy arrays.")

            if numpyarray.ndim != 2:

                raise RuntimeError("Your array must be 2D.")

            self.numpyarray = numpyarray.astype(np.float32)

        # We keep trace of any crops through these :
        self.xa = 0
        self.ya = 0
        self.xb = self.numpyarray.shape[0]
        self.yb = self.numpyarray.shape[1]
        self.origwidth = self.xb
        self.origheight = self.yb
        # They will be updated by any crop method, so that we can always convert between
        # coordinates in the orinigal numpy array or fits image, and coordinates in the
        # rebinned cutout etc.

    def __str__(self):
        """
        Returns a string with some info about the image.
        The details vary according to what you have done so far with the image.
        """

        return_string = ["Numpy array shape : ", str(self.numpyarray.shape)]

        if self.xa != 0 or self.ya != 0 or self.xb != self.origwidth or self.yb != self.origheight:
            return_string.append("\nRegion : [%i:%i, %i:%i]" % (self.xa, self.xb, self.ya, self.yb))

        return_string.extend([
            "\nPixel type : %s" % str(self.numpyarray.dtype.name),
            "\nCutoffs : z1 = %f, z2=%f" % (self.z1, self.z2)
        ])

        if self.pilimage is not None:
            return_string.extend([
                "\nPIL image mode : %s" % str(self.pilimage.mode),
                "\nPIL image shape : (%i,  %i)" % (self.pilimage.size[0], self.pilimage.size[1])
            ])

        return ''.join(return_string)

    def setzscale(self, z1="auto", z2="auto", nsig=3, samplesizelimit=10000, border=300, satlevel=65000):
        """
        We set z1 and z2, according to different algorithms or arguments.

        For both z1 and z2, give either :

            - "auto" (default automatic, separate computations done to estimate z1 and z2)
            - "ex" (extrema)
            - "flat" ("sigma-cuts" around median value, well-suited for flatfields)
            - numeric value like 1230.34

        nsig is the number of sigmas to be rejected (used by auto z1 + both flats)

        samplesizelimit is the maximum number of pixels to compute statistics on.
        If your image is larger than samplesizelimit, I will use only samplesizelimit pixels of it.

        If your image is 3 times border in width and height, I will skip border pixels around the image before
        doing calculations. This is made to get rid of the overscan and prescan etc.
        So you can basically leave this at 300, it will only affect images wider than 900 pixels.
        (300 happens to be a safe value for many telescopes.)
        You can put border = 0 to deactivate this feature.

        If satlevel is > 0, pixels higher than satlevel will be disregarde in the statistics.
        So you can set satlevel < 0 to turn off this feature.

        If you give nothing, the cutoff will not be changed.
        You should set the z scale directly after cropping the image.

        """

        if self.pilimage is not None:
            raise RuntimeError("Cannot set z scale anymore, PIL image already exists !")

        if self.numpyarray.shape[0] > 3 * border and self.numpyarray.shape[1] > 3 * border:
            if border > 0:
                if self.verbose :
                    print(("For the stats I will leave a border of %i pixels" % border))
                calcarray = self.numpyarray[border:-border, border:-border].copy()
            else:
                calcarray = self.numpyarray.copy()
        else:
            calcarray = self.numpyarray.copy()
            if self.verbose:
                print(("Image is too small for a border of %i" % (border)))
        calcarray.shape = calcarray.size  # We flatten the 2D array

        # Now we handle an eventual saturation so that it does not fool the code :
        if satlevel > 0:
            calcarray = calcarray[calcarray < satlevel]  # we simply skip pixels higher than satlevel...

        # Starting with the simple possibilities :
        if z1 == "ex":
            self.z1 = np.min(calcarray)
            if self.verbose:
                print(("Setting ex z1 to %f" % self.z1))

        if z2 == "ex":
            self.z2 = np.max(calcarray)
            if self.verbose:
                print(("Setting ex z2 to %f" % self.z2))

        if type(z1) == type(0) or type(z1) == type(0.0):
            self.z1 = z1
            if self.verbose:
                print(("Setting z1 to %f" % self.z1))

        if type(z2) == type(0) or type(z2) == type(0.0):
            self.z2 = z2
            if self.verbose:
                print(("Setting z2 to %f" % self.z2))

        # Now it gets a little more sophisticated.
        if z1 == "auto" or z2 == "auto" or z1 == "flat" or z2 == "flat":
            # To speed up, we do not want to do statistics on the full image if it is large.
            # So we prepare a small random sample of pixels.
            calcarray.shape = calcarray.size  # We flatten the 2D array
            if calcarray.size > samplesizelimit:
                selectionindices = np.linspace(0, calcarray.size-1, samplesizelimit).astype(int)
                statsel = calcarray[selectionindices]
            else:
                statsel = calcarray

            medianlevel = np.median(statsel)
            firststd = np.std(statsel)

            if z1 == "auto":
                # 2 sigma clipping (quick and dirty star removal) :
                nearskypixvals = statsel[np.logical_and(statsel > medianlevel - 2*firststd,
                                                        statsel < medianlevel + 2*firststd)]
                skylevel = np.median(nearskypixvals)
                secondstd = np.std(nearskypixvals)
                if self.verbose:
                    print(("Sky level at %f +/- %f" % (skylevel, secondstd)))

                self.z1 = skylevel - nsig*secondstd
                if self.verbose:
                    print(("Setting auto z1 to %f, nsig = %i" % (self.z1, nsig)))

            if z2 == "auto":
                # Here we want to reject a percentage of high values...
                self.z2 = np.nanpercentile(statsel, 99.95)
                if self.verbose:
                    print(("Setting auto z2 to %f" % self.z2))

            if z1 == "flat":

                # 5 sigma clipping to get rid of cosmics :
                nearflatpixvals = statsel[np.logical_and(statsel > medianlevel - 5*firststd,
                                                         statsel < medianlevel + 5*firststd)]

                flatlevel = np.median(nearflatpixvals)
                flatstd = np.std(nearflatpixvals)

                self.z1 = flatlevel - nsig*flatstd

                if self.verbose:
                    print(("Setting flat z1 : %f, nsig = %i" % (self.z1, nsig)))

            if z2 == "flat":
                # 5 sigma clipping to get rid of cosmics :
                nearflatpixvals = statsel[np.logical_and(statsel > medianlevel - 5*firststd,
                                                         statsel < medianlevel + 5*firststd)]

                flatlevel = np.median(nearflatpixvals)
                flatstd = np.std(nearflatpixvals)

                self.z2 = flatlevel + nsig*flatstd

                if self.verbose:
                    print(("Setting flat z2 : %f, nsig = %i" % (self.z2, nsig)))

            if np.isnan(self.z1):
                self.z1 = 0.0
                if self.verbose:
                    print("Correcting NaN z1")

            if np.isnan(self.z2):
                self.z2 = 0.0
                if self.verbose:
                    print("Correcting NaN z2")

    def crop(self, xa, xb, ya, yb):
        """
        Crops the image. Two points :

            - We use numpy conventions
            xa = 200 and xb = 400 will give you a width of 200 pixels !

            - We crop relative to the current array (i.e. not necessarily to the original array !)
            This means you can crop several times in a row with xa = 10, it will each time remove 10 pixels in x !

        But we update the crop region specifications, so that the object remembers how it was cut.

        Please give positive integers in compatible ranges, no checks are made.

        """

        if self.pilimage is not None:
            raise RuntimeError("Cannot crop anymore, PIL image already exists !")

        if self.verbose:
            print(("Cropping : [%i:%i, %i:%i]" % (xa, xb, ya, yb)))
        self.numpyarray = self.numpyarray[xa:xb, ya:yb]

        self.xa += xa
        self.ya += ya
        self.xb = self.xa + (xb - xa)
        self.yb = self.ya + (yb - ya)

    def irafcrop(self, irafcropstring):
        """
        This is a wrapper around crop(), similar to iraf imcopy,
        using iraf conventions (100:199 will be 100 pixels, not 99).
        """
        irafcropstring = irafcropstring[1:-1]  # removing the [ ]
        ranges = irafcropstring.split(",")
        xr = ranges[0].split(":")
        yr = ranges[1].split(":")
        xmin = int(xr[0])
        xmax = int(xr[1])+1
        ymin = int(yr[0])
        ymax = int(yr[1])+1
        self.crop(xmin, xmax, ymin, ymax)

    def rebin(self, factor, method="mean"):
        """
        I robustly rebin your image by a given factor.
        You simply specify a factor, and I will eventually take care of a crop to bring
        the image to interger-multiple-of-your-factor dimensions.
        Note that if you crop your image before, you must directly crop to compatible dimensions !
        We update the binfactor, this allows you to draw on the image later, still using the
        orignial pixel coordinates.
        Here we work on the numpy array.

        method = "mean"
        method = "max" : to get the max value of each bin
        """

        if self.pilimage is not None:
            raise RuntimeError("Cannot rebin anymore, PIL image already exists !")

        if type(factor) != type(0):
            raise RuntimeError("Rebin factor must be an integer !")

        if factor < 1:
            return

        origshape = np.asarray(self.numpyarray.shape)
        neededshape = origshape - (origshape % factor)
        if not (origshape == neededshape).all():
            if self.verbose:
                print(("Rebinning %ix%i : I have to crop from %s to %s" % (factor, factor, origshape, neededshape)))
            self.crop(0, neededshape[0], 0, neededshape[1])
        else:
            if self.verbose:
                print(("Rebinning %ix%i : I do not need to crop" % (factor, factor)))

        if method == "mean":
            self.numpyarray = rebin(self.numpyarray, neededshape/factor)  # we call the rebin function defined below
        elif method == "max":
            if self.verbose:
                print("submodules - Getting the MAX out of your images !")
            self.numpyarray = remax(self.numpyarray, neededshape/factor)
        else:
            raise RuntimeError("Unknown rebin method %s" % method)
        # The integer division neededshape/factor is ok, we checked for this above.
        self.binfactor = int(self.binfactor * factor)

    def makepilimage(self, scale="log", negative=False):
        """
        Makes a PIL image out of the array, respecting the z1 and z2 cutoffs.
        By default we use a log scaling identical to iraf's, and produce an image of mode "L", i.e. grayscale.
        But some drawings or colourscales will change the mode to "RGB" later, if you choose your own colours.
        If you choose scale = "clog" or "clin", you get hue values (aka rainbow colours).
        """

        calcarray = self.numpyarray.transpose()

        if scale == "log" or scale == "lin":
            self.negative = negative

            calcarray = calcarray.clip(min=self.z1, max=self.z2)

            if scale == "log":
                calcarray = loggray(calcarray, self.z1, self.z2)
            else:

                calcarray = lingray(calcarray, self.z1, self.z2)

            bwarray = np.zeros(calcarray.shape)
            calcarray.round(out=bwarray)
            bwarray = bwarray.astype(np.uint8)  # and you get the dtype you want in the end
            if negative:
                if self.verbose:
                    print("Using negative scale")
                bwarray = 255 - bwarray

            if self.verbose:
                print(("PIL range : [%i, %i]" % (np.min(bwarray), np.max(bwarray))))

            # We flip it so that (0, 0) is back in the bottom left corner as in ds9
            # We do this here, so that you can write on the image from left to right :-)

            self.pilimage = imop.flip(im.fromarray(bwarray))
            if self.verbose:
                print(("PIL image made with scale : %s" % scale))
            return 0

        if scale == "clog" or scale == "clin":  # Rainbow !

            self.negative = False
            if scale == "clin":
                calcarray = (calcarray.clip(min=self.z1, max=self.z2)-self.z1)/(self.z2 - self.z1)  # 0 to 1
            if scale == "clog":
                calcarray = 10.+990.*(calcarray.clip(min=self.z1, max=self.z2)-self.z1)/(self.z2-self.z1)  # 10 to 1000
                calcarray = (np.log10(calcarray)-1.0)*0.5  # 0 to 1

            (rarray, garray, barray) = rainbow(calcarray, autoscale=False)
            carray = np.dstack((rarray, garray, barray))

            self.pilimage = imop.flip(im.fromarray(carray, "RGB"))
            if self.verbose:
                print(("PIL image made with scale : %s" % scale))
            return 0

        raise RuntimeError("I don't know your colourscale, choose lin log clin or clog !")

    def drawmask(self, maskarray, colour=128):
        """
        I draw a mask on the image.
        Give me a numpy "maskarray" of same size as mine, and I draw on the pilimage all pixels
        of the maskarray that are True in the maskcolour.
        By default, colour is gray, to avoid switching to RGB.
        But if you give for instance (255, 0, 0), I will do the switch.
        """
        self.checkforpilimage()
        self.changecolourmode(colour)
        self.makedraw()

        # Checking size of maskarray :
        if maskarray.shape[0] != self.pilimage.size[0] or maskarray.shape[1] != self.pilimage.size[1]:
            raise RuntimeError("Mask and image must have the same size !")

        # We make an "L" mode image out of the mask :
        tmparray = np.zeros(maskarray.shape, dtype=np.uint8)
        tmparray[maskarray] = 255
        maskpil = imop.flip(im.fromarray(tmparray.transpose()))

        # We make a plain colour image :
        if type(colour) == type(0):
            plainpil = im.new("L", self.pilimage.size, colour)
        else:
            plainpil = im.new("RGB", self.pilimage.size, colour)

        # We switch self to RGB if needed :
        self.changecolourmode(colour)

        # And now use the function composite to "blend" our image with the plain colour image :

        self.pilimage = im.composite(plainpil, self.pilimage, maskpil)

        # As we have changed the image object, we have to rebuild the draw object :
        self.draw = None

    def showcutoffs(self, redblue=False):
        """
        We use drawmask to visualize pixels above and below the z cutoffs.
        By default this is done in black (above) and white (below) (and adapts to negative images).
        But if you choose redblue = True, I use red for above z2 and blue for below z1.
        """

        highmask = self.numpyarray > self.z2
        lowmask = self.numpyarray < self.z1
        if not redblue:
            if self.negative:
                self.drawmask(highmask, colour=255)
                self.drawmask(lowmask, colour=0)
            else:
                self.drawmask(highmask, colour=0)
                self.drawmask(lowmask, colour=255)
        else:

            self.drawmask(highmask, colour=(255, 0, 0))
            self.drawmask(lowmask, colour=(0, 0, 255))

    def checkforpilimage(self):
        """Auxiliary method to check if the PIL image was already made."""
        if self.pilimage is None:
            raise RuntimeError("No PIL image : call makepilimage first !")

    def makedraw(self):
        """Auxiliary method to make a draw object if not yet done.
        This is also called by changecolourmode, when we go from L to RGB, to get a new draw object.
        """
        if self.draw is None:
            self.draw = imdw.Draw(self.pilimage)

    def defaultcolour(self, colour):
        """
        Auxiliary method to choose a default colour.
        Give me a user provided colour : if it is None, I change it to the default colour, respecting negative.
        Plus, if the image is in RGB mode, and you give me 128 for a gray,
        I translate this to the expected (128, 128, 128) ...
        """
        if colour is None:
            if self.negative:
                if self.pilimage.mode == "L":
                    return 0
                else:
                    return (0, 0, 0)
            else:
                if self.pilimage.mode == "L":
                    return 255
                else:
                    return (255, 255, 255)
        else:
            if self.pilimage.mode == "RGB" and type(colour) == type(0):
                return colour, colour, colour
            else:
                return colour

    def changecolourmode(self, newcolour):
        """Auxiliary method to change the colour mode.
        Give me a colour (either an int, or a 3-tuple, values 0 to 255) and I decide if the image mode has to
        be switched from "L" to "RGB".
        """
        if type(newcolour) != type(0) and self.pilimage.mode != "RGB":
            if self.verbose:
                print("Switching to RGB !")
            self.pilimage = self.pilimage.convert("RGB")
            self.draw = None  # important, we have to bebuild the draw object.
            self.makedraw()

    def upsample(self, factor):
        """
        The inverse operation of rebin, applied on the PIL image.
        Do this before writing text or drawing on the image !
        The coordinates will be automatically converted for you
        """

        self.checkforpilimage()

        if self.verbose:
            print(("Upsampling by a factor of %i" % factor))
        self.pilimage = self.pilimage.resize((int(self.pilimage.size[0] * factor),
                                              int(self.pilimage.size[1] * factor)))
        self.upsamplefactor = factor

        self.draw = None

    def pilcoords(self, xxx_todo_changeme):
        """
        Converts the coordinates (x,y) of the original array or FITS file to the current coordinates of the PIL image,
        respecting cropping, rebinning, and upsampling.
        This is only used once the PIL image is available, for drawing.
        Note that we also have to take care about the different origin conventions here !
        For PIL, (0,0) is top left, so the y-axis needs to be inverted.
        """
        (x, y) = xxx_todo_changeme
        pilx = int((x - 1 - self.xa) * float(self.upsamplefactor) / float(self.binfactor))
        pily = int((self.yb - y) * float(self.upsamplefactor) / float(self.binfactor))

        return (pilx, pily)

    def pilscale(self, r):
        """
        Converts a "scale" (like an aperture radius) of the original array or FITS file to the current PIL coordinates.
        """
        return r * float(self.upsamplefactor) / float(self.binfactor)

    def drawpoint(self, x, y, colour=None):
        """
        Most elementary drawing, single pixel, used mainly for testing purposes.
        Coordinates are those of your initial image !
        """
        self.checkforpilimage()
        colour = self.defaultcolour(colour)
        self.changecolourmode(colour)
        self.makedraw()

        (pilx, pily) = self.pilcoords((x, y))

        self.draw.point((pilx, pily), fill=colour)

    def drawcircle(self, x, y, r=10, colour=None, label=None):
        """
        Draws a circle centered on (x, y) with radius r. All these are in the coordinates of your initial image !
        You give these x and y in the usual ds9 pixels, (0,0) is bottom left.
        I will convert this into the right PIL coordiates.
        """

        self.checkforpilimage()
        colour = self.defaultcolour(colour)
        self.changecolourmode(colour)
        self.makedraw()

        (pilx, pily) = self.pilcoords((x, y))
        pilr = self.pilscale(r)

        self.draw.ellipse([(pilx-pilr+1, pily-pilr+1), (pilx+pilr+1, pily+pilr+1)], outline=colour)

        if label is not None:
            # Then we write it :
            textwidth = 5
            self.draw.text((pilx - float(textwidth)/2.0 + 2, pily + pilr + 4), label, fill=colour, font=self.labelfont)

    def drawrectangle(self, xa, xb, ya, yb, colour=None, label=None):
        """
        Draws a 1-pixel wide frame AROUND the region you specify. Same convention as for crop().

        """

        self.checkforpilimage()
        colour = self.defaultcolour(colour)
        self.changecolourmode(colour)
        self.makedraw()

        (pilxa, pilya) = self.pilcoords((xa, ya))
        (pilxb, pilyb) = self.pilcoords((xb, yb))

        self.draw.rectangle([(pilxa, pilyb-1), (pilxb+1, pilya)], outline=colour)

        if label is not None:
            textwidth = 5
            self.draw.text(((pilxa + pilxb)/2.0 - float(textwidth)/2.0 + 1, pilya + 2), label,
                           fill=colour, font=self.labelfont)

    def drawline(self, x=None, y=None, l=10, t=0.0, width=None, colour=None):
        """
        Draws a 1-pixel wide line, centered on x y with length l and angle t (in rad).
        Can be used to represent ellipticities, for instance.
        x y : center (if None, I'll draw it in the center of the image)
        l : length
        t : theta (positive geometric convention)

        label is not implemented.
        """

        if x is None:
            x = self.origwidth/2.0
        if y is None:
            y = self.origheight/2.0

        self.checkforpilimage()
        colour = self.defaultcolour(colour)
        self.changecolourmode(colour)
        self.makedraw()

        ax = x - 0.5*l*np.cos(t) + 1
        ay = y - 0.5*l*np.sin(t)
        bx = x + 0.5*l*np.cos(t) + 1
        by = y + 0.5*l*np.sin(t)

        (pilax, pilay) = self.pilcoords((ax, ay))
        (pilbx, pilby) = self.pilcoords((bx, by))

        # Ugly trick to avoid documented bug of draw.line :
        if width is None:
            self.draw.line([(pilax, pilay), (pilbx, pilby)], fill=colour)
        else:
            self.draw.line([(pilax, pilay), (pilbx, pilby)], width=int(width), fill=colour)

    def writetitle(self, titlestring, colour=None):
        """
        We write a title, centered below the image.
        """

        self.checkforpilimage()
        colour = self.defaultcolour(colour)
        self.changecolourmode(colour)
        self.makedraw()

        imgwidth = self.pilimage.size[0]
        imgheight = self.pilimage.size[1]
        textwidth = 5
        textxpos = imgwidth/2.0 - textwidth/2.0
        textypos = imgheight - 30

        self.draw.text((textxpos, textypos), titlestring, fill=colour, font=self.titlefont)

        if self.verbose:
            print("I've written a title on the image.")

    def writeinfo(self, linelist, colour=None):
        """
        We add a longer chunk of text in the upper left corner of the image.
        Provide linelist, a list of strings that will be written one below the other.
        """

        self.checkforpilimage()
        colour = self.defaultcolour(colour)
        self.changecolourmode(colour)
        self.makedraw()

        for i, line in enumerate(linelist):
            topspacing = 5 + (12 + 5)*i
            self.draw.text((10, topspacing), line, fill=colour, font=self.infofont)

        if self.verbose:
            print("I've written some info on the image.")

    def drawstarlist(self, starlist, r=10, colour=None, autocolour=None):
        """
        Calls drawcircle and writelabel for a list of stars.
        starlist is a list of either:
            - dictionnaries, with fields "name", "x", and "y"
            - objects with attributes name, x, and y --> use this if you work with the star.py module.
        In both cases, you can optinnally also provide "r" and "colour" for each star.

        :param autocolour: name of a key or attribute to use to determine colour (e.g., 'flux', 'fwhm')
        ... Individual colours are disregarded in this case.
        :type autocolour: string

        """

        if len(starlist) == 0:
            if self.verbose:
                print("No stars to draw !")
            return

        if autocolour is not None and len(starlist) >= 2:
            colours = []
            for star in starlist:
                if type(star) is dict:
                    colours.append(star.get(autocolour, 0.0))
                else:
                    colours.append(getattr(star, autocolour, 0.0))
            colours = np.array(colours)
            colours = loggray(colours)

            (rarray, garray, barray) = rainbow(colours, autoscale=True)
            colours = np.dstack((rarray, garray, barray))[0]

        else:
            colours = []
            for star in starlist:
                if type(star) is dict:
                    colours.append(star.get("colour", colour))
                else:
                    colours.append(getattr(star, "colour", colour))

        self.checkforpilimage()
        colour = self.defaultcolour(colours[0])
        self.changecolourmode(colour)
        self.makedraw()

        for (star, c) in zip(starlist, colours):
            if c is not None:
                c = tuple(c)
            if type(star) is dict:
                self.drawcircle(star["x"], star["y"], r=star.get("r", r), colour=c, label=star["name"])
            else:
                self.drawcircle(star.x, star.y, getattr(star, "r", r), colour=c, label=star.name)

        if self.verbose:
            print(("I've drawn %i stars." % len(starlist)))

    def drawstarfile(self, filename, r=10, colour=None):
        """
        Same as drawstarlist but we read the stars from a file.
        Here we read a text file of hand-picked stars. Same format as for cosmouline, that is :
        # comment
        starA	23.4	45.6	[other stuff...]
        Then we pass this to drawstarlist,
        """
        if not os.path.isfile(filename):
            print("File does not exist :")
            print(filename)
            print("Line format to write : name x y [other stuff ...]")
            raise RuntimeError("Cannot read star catalog.")

        catfile = open(filename, "r")
        lines = catfile.readlines()
        catfile.close()

        dictlist = []  # We will append dicts here.
        for i, line in enumerate(lines):
            if line[0] == '#' or len(line) < 4:
                continue
            elements = line.split()
            nbelements = len(elements)
            if nbelements < 3:
                print(("Format error on line", i+1, "of :"))
                print(filename)
                print("We want : name x y [other stuff ...]")
                raise RuntimeError("Cannot read star catalog.")

            name = elements[0]
            x = float(elements[1])
            y = float(elements[2])
            dictlist.append({"name": name, "x": x, "y": y})

        if self.verbose:
            print("I've read %i stars from :")
            print((os.path.split(filename)[1]))

        self.drawstarlist(dictlist, r=r, colour=colour)

    def tonet(self, outfile):
        """
        Writes the PIL image into a png.
        We do not want to flip the image at this stage, as you might have written on it !
        """

        self.checkforpilimage()
        if self.verbose:
            print(("Writing image to %s...\n%i x %i pixels, mode %s" % (outfile, self.pilimage.size[0],
                                                                        self.pilimage.size[1], self.pilimage.mode)))
        self.pilimage.save(outfile, "PNG")


def lingray(x, a=None, b=None):
    """
    Auxiliary function that specifies the linear gray scale.
    a and b are the cutoffs : if not specified, min and max are used
    """
    if a is None:
        a = np.min(x)
    if b is None:
        b = np.max(x)

    return 255.0 * (x-float(a))/(b-a)


def loggray(x, a=None, b=None):
    """
    Auxiliary function that specifies the logarithmic gray scale.
    a and b are the cutoffs : if not specified, min and max are used
    """
    if a is None:
        a = np.min(x)
    if b is None:
        b = np.max(x)

    linval = 10.0 + 990.0 * (x-float(a))/(b-a)
    return (np.log10(linval)-1.0)*0.5 * 255.0


def rainbow(data, autoscale=False):
    """
    Give me array-like intensities/fluxes/whatever, I return uint8 arrays of red, green, blue.

    :param data: the data. Should be between 0.0 and 1.0, otherwise, use autoscale
    :type data: array

    :param autoscale: I rescale the data to be between 0.0 and 1.0
    :type autoscale: boolean

    Algorithm for HSV to RGB from http://www.cs.rit.edu/~ncs/color/t_convert.html, by Eugene Vishnevsky
    Same stuff then for submodules in C

    h is from 0 to 360 (hue)
    s from 0 to 1 (saturation)
    v from 0 to 1 (brightness)
    """

    if autoscale:
        calcarray = (data - np.min(data)) / (np.max(data) - np.min(data))
    else:
        calcarray = data.copy()

    calcarray = (1.0-calcarray) * 300.0  # I limit this to not go into red again
    # The order of colours is Violet < Blue < Green < Yellow < Red

    # We prepare the output arrays
    rcalcarray = np.ones(calcarray.shape)
    gcalcarray = np.ones(calcarray.shape)
    bcalcarray = np.ones(calcarray.shape)

    h = calcarray/60.0  # sector 0 to 5
    i = np.floor(h).astype(int)

    v = 1.0 * np.ones(calcarray.shape)
    s = 1.0 * np.ones(calcarray.shape)

    f = h - i  # factorial part of h, this is an array
    p = v * ( 1.0 - s )
    q = v * ( 1.0 - s * f )
    t = v * ( 1.0 - s * ( 1.0 - f ) )

    # sector 0:
    indices = (i == 0)
    rcalcarray[indices] = 255.0 * v[indices]
    gcalcarray[indices] = 255.0 * t[indices]
    bcalcarray[indices] = 255.0 * p[indices]

    # sector 1:
    indices = (i == 1)
    rcalcarray[indices] = 255.0 * q[indices]
    gcalcarray[indices] = 255.0 * v[indices]
    bcalcarray[indices] = 255.0 * p[indices]

    # sector 2:
    indices = (i == 2)
    rcalcarray[indices] = 255.0 * p[indices]
    gcalcarray[indices] = 255.0 * v[indices]
    bcalcarray[indices] = 255.0 * t[indices]

    # sector 3:
    indices = (i == 3)
    rcalcarray[indices] = 255.0 * p[indices]
    gcalcarray[indices] = 255.0 * q[indices]
    bcalcarray[indices] = 255.0 * v[indices]

    # sector 4:
    indices = (i == 4)
    rcalcarray[indices] = 255.0 * t[indices]
    gcalcarray[indices] = 255.0 * p[indices]
    bcalcarray[indices] = 255.0 * v[indices]

    # sector 5:
    indices = (i == 5)
    rcalcarray[indices] = 255.0 * v[indices]
    gcalcarray[indices] = 255.0 * p[indices]
    bcalcarray[indices] = 255.0 * q[indices]

    rarray = np.zeros(calcarray.shape, dtype=np.uint8)
    garray = np.zeros(calcarray.shape, dtype=np.uint8)
    barray = np.zeros(calcarray.shape, dtype=np.uint8)
    rcalcarray.round(out=rarray)
    gcalcarray.round(out=garray)
    bcalcarray.round(out=barray)

    return (rarray, garray, barray)


def fromfits(infile, hdu=0, verbose=True):
    """
    Factory function that reads a FITS file and returns a f2nimage object.
    Use hdu to specify which HDU you want (primary = 0)
    """

    pixelarray, hdr = ft.getdata(infile, hdu, header=True)
    pixelarray = np.asarray(pixelarray).transpose()

    pixelarrayshape = pixelarray.shape
    if verbose:
        print(("Input shape : (%i, %i)" % (pixelarrayshape[0], pixelarrayshape[1])))
        print(("Input file BITPIX : %s" % (hdr["BITPIX"])))
    if verbose:
        print(("Internal array type :", pixelarray.dtype.name))

    return f2nimage(pixelarray, verbose=verbose)


def rebin(a, newshape):
    """
    Auxiliary function to rebin ndarray data.
    Source : http://www.scipy.org/Cookbook/Rebinning
        example usage:
     a=rand(6,4); b=rebin(a,(3,2))
    """

    shape = np.shape(a)

    lenShape = len(shape)

    evList = ['a.reshape('] + \
             ['int(newshape[%d]),int(factor[%d]),' % (i, i) for i in range(lenShape)] + \
             [')'] + ['.sum(%d)' % (i+1) for i in range(lenShape)] + \
             ['/int(factor[%d])' % i for i in range(lenShape)]

    return eval(''.join(evList))


def remax(a, newshape):
    """
    Aux function to "rebin" an array but always keeping the maximum pixel value of each
    bin instead of the mean !
    """

    shape = a.shape

    lenShape = len(shape)

    factor = np.asarray(shape)/np.asarray(newshape)

    evList = ['a.reshape('] + \
             ['int(newshape[%d]),int(factor[%d]),' % (i, i) for i in range(lenShape)] + \
             [')'] + ['.max(%d)' % (i+1) for i in range(lenShape)]

    return eval(''.join(evList))


def compose(f2nimages, outfile):
    """
    Takes f2nimages and writes them into one single png file, side by side.
    f2nimages is a list of horizontal lines, where each line is a list of f2nimages.
    For instance :
    [
    [image1, image2],
    [image3, image4]
    ]
    The sizes of these images have to "match", so that the final result is rectangular.

    This function is verbose if any of the images is verbose.

    """
    # We start by doing some checks, and try to print out helpfull error messages.
    verbosity = []
    colourmodes = []
    for i, line in enumerate(f2nimages):
        for j, img in enumerate(line):
            if img.verbose:
                print(("Checking line %i, image %i (verbose)..." % (i+1, j+1)))
            img.checkforpilimage()
            verbosity.append(img.verbose)
            colourmodes.append(img.pilimage.mode)
    verbose = np.any(np.array(verbosity))
    colours = list(set(colourmodes))

    # We check if the widths are compatible :
    widths = [np.sum(np.array([img.pilimage.size[0] for img in line])) for line in f2nimages]
    if len(set(widths)) != 1:
        print("Total widths of the lines :")
        print(widths)
        raise RuntimeError("The total widths of your lines are not compatible !")
    totwidth = widths[0]

    # Similar for the heights :
    for i, line in enumerate(f2nimages):
        heights = [img.pilimage.size[1] for img in line]
        if len(set(heights)) != 1:
            print(("Heights of the images in line %i :" % (i + 1)))
            print(heights)
            raise RuntimeError("Heights of the images in line %i are not compatible." % (i + 1))

    totheight = np.sum(np.array([line[0].pilimage.size[1] for line in f2nimages]))
    # Ok, now it should be safe to go for the composition :
    if verbose:
        print(("Composition size : %i x %i" % (totwidth, totheight)))

    if verbose:
        print(("Colour modes of input : %s" % colours))
    if len(colours) == 1 and colours[0] == "L":
        if verbose:
            print("Builing graylevel composition")
        compoimg = im.new("L", (totwidth, totheight), 128)
    else:
        if verbose:
            print("Building RGB composition")
        compoimg = im.new("RGB", (totwidth, totheight), (255, 0, 0))

    y = 0
    for line in f2nimages:
        x = 0
        for img in line:
            box = (x, y, x+img.pilimage.size[0], y+img.pilimage.size[1])
            compoimg.paste(img.pilimage, box)
            x += img.pilimage.size[0]
        y += img.pilimage.size[1]

    if verbose:
        print(("Writing compositions to %s...\n%i x %i pixels, mode %s" % (outfile, compoimg.size[0],
                                                                           compoimg.size[1], compoimg.mode)))
    compoimg.save(outfile, "PNG")


def isnumeric(value):
    """
    "0.2355" would return True.
    A little auxiliary function for command line parsing.
    """
    return str(value).replace(".", "").replace("-", "").isdigit()
