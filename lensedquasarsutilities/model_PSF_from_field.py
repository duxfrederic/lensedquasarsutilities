"""
 Collection of helpers to get a nice PSF for the field of a lens in a given survey and filter.
"""
import numpy as np
from pathlib import Path
import matplotlib.pyplot as plt
import io

from starred.psf.psf import PSF
from starred.psf.loss import Loss
from starred.psf.parameters import ParametersPSF
from starred.utils.generic_utils import save_fits
from starred.utils.optimization import Optimizer
from starred.utils import ds9reg
from starred.plots import plot_function as ptf
from starred.utils.noise_utils import propagate_noise

from lensedquasarsutilities.gaia_utilities import get_similar_stars
from lensedquasarsutilities.downloader import get_cutouts_file
from lensedquasarsutilities.stamp_extractor import extract_stamps
from lensedquasarsutilities import config
from lensedquasarsutilities.formatting import get_J2000_name
from lensedquasarsutilities.io import save_dict_to_hdf5, update_hdf5, load_dict_from_hdf5
from lensedquasarsutilities.plots import plot_psf


def create_round_mask(size, radius):
    # Create a grid of indices
    x, y = np.indices((size, size))

    center = (size - 1) / 2

    distance = np.sqrt((x - center) ** 2 + (y - center) ** 2)

    arr = np.full((size, size), True)
    arr[distance <= radius] = False

    return arr


def estimate_psf_from_extracted_h5(h5filepath):
    """
    Here we'll open the file created with `download_and_extract`, and
    for each band
        for each image
             estimate the PSF and store it in the same hdf5 file.

    :param h5filepath: string or Path, path to our cutouts.
    :return: None
    """

    dic = load_dict_from_hdf5(h5filepath)
    for band, banddata in dic.items():
        for imageindex, objects in banddata.items():
            stars = objects['stars']
            noisemaps = objects['noise']
            hsize = stars.shape[1]
            masks = create_round_mask(hsize, 0.4*hsize)  # yeaaaaaaah I don't want to deal with masking yet.
            masks = np.repeat(masks[np.newaxis, ...], stars.shape[0], axis=0)

            narrowpsf, fullmodel, loss_history = estimate_psf(stars, noisemaps**2, masks, upsampling_factor=2)
            # store the estimated PSF in the hdf5 file.
            update_hdf5(h5filepath, f"{band}/{imageindex}/psf", narrowpsf)

            ############################################################################################################
            # plot!
            identifier = f"{band}_{imageindex}"
            residuals = (stars - fullmodel) / noisemaps
            # ok, here we make the plot of the loss history.
            try:
                # try because the analytical methods don't have a 'loss_history'
                # field.
                fig = plt.figure(figsize=(2.56, 2.56))
                plt.plot(loss_history)
                plt.title('loss history')
                plt.tight_layout()
                with io.BytesIO() as buff:
                    # write the plot to a buffer, read it with numpy
                    fig.savefig(buff, format='raw')
                    buff.seek(0)
                    plotimg = np.frombuffer(buff.getvalue(), dtype=np.uint8)
                    w, h = fig.canvas.get_width_height()
                    # white <-> black:
                    lossim = 255 - plotimg.reshape((int(h), int(w), -1))[:, :, 0].T[:, ::-1]
                plt.close()
            except:
                print('no loss history in extra_fields')
                lossim = np.zeros((256, 256))
            plot_psf(identifier, noisemaps, stars, residuals, narrowpsf, lossim, workdir=Path(h5filepath).parent)
            ############################################################################################################


def estimate_psf(stars, sigma_2, masks, upsampling_factor=2):
    """
    Final step once we have the data, the right stars and their cutouts and noise maps, and potentially masks.

    :param stars:  3D array, shape (N, nx, ny) where N is the number of star cutouts, and nx, ny the cutout dimensions
    :param sigma_2: same as `stars`, but for the noisemap (squared).
    :param masks: same as `stars` if applicable, masks to apply to the field. default: None
    :param upsampling_factor: pixel size of PSF model / pixel size of image. Default: 2
    :return: 2D numpy array of the PSF of the field, model of the given stars with the obtained PSF, loss history
    """
    # let's scale our data!
    scale = np.nanpercentile(stars, 99.9)
    stars /= scale
    sigma_2 /= scale**2

    # save a copy of noise:
    noise_for_W = np.sqrt(sigma_2.copy())
    # mask:
    # sigma_2[masks] = 1e15
    N = stars.shape[0]
    image_size = stars[0].shape[0]

    # Positions
    x0_est = np.array([0. for i in range(N)])
    y0_est = np.array([0. for i in range(N)])

    model = PSF(image_size=image_size, number_of_sources=N,
                upsampling_factor=upsampling_factor,
                convolution_method='fft', include_moffat=True)

    # Parameter initialization
    kwargs_init, kwargs_fixed, kwargs_up, kwargs_down = model.smart_guess(stars, fixed_background=True)
    kwargs_init['kwargs_moffat']['x0'] = x0_est
    kwargs_init['kwargs_moffat']['y0'] = y0_est

    # first, moffat only
    kwargs_fixed = {
        'kwargs_moffat': {},
        'kwargs_gaussian': {},
        'kwargs_background': kwargs_init['kwargs_background'],
    }

    parameters = ParametersPSF(model, kwargs_init, kwargs_fixed,
                               kwargs_up=kwargs_up,
                               kwargs_down=kwargs_down)
    loss = Loss(stars, model, parameters, sigma_2, N, regularization_terms='l1_starlet',
                regularization_strength_scales=1.0, regularization_strength_hf=1.0,  # doesn't matter here
                regularize_full_psf=False, masks=~masks)

    optim = Optimizer(loss, parameters, method='l-bfgs-b')
    best_fit, logL_best_fit, extra_fields, runtime = optim.minimize(maxiter=30)
    L1 = optim.loss_history

    # now doing background only
    kwargs_partial = parameters.args2kwargs(best_fit)

    W = propagate_noise(model, noise_for_W, kwargs_partial,
                        wavelet_type_list=['starlet'], method='MC',
                        num_samples=100,
                        seed=1, likelihood_type='chi2', verbose=False,
                        upsampling_factor=upsampling_factor)[0]

    # Background tuning, fixing Moffat
    kwargs_fixed = {
        'kwargs_moffat': kwargs_partial['kwargs_moffat'],
        'kwargs_gaussian': kwargs_partial['kwargs_gaussian'],
        'kwargs_background': {},
    }

    parameters = ParametersPSF(model, kwargs_partial, kwargs_fixed,
                               kwargs_up=kwargs_up,
                               kwargs_down=kwargs_down)
    loss = Loss(stars, model, parameters, sigma_2, N, regularization_terms='l1_starlet',
                regularization_strength_scales=1.0, regularization_strength_hf=1.0,
                regularization_strength_positivity=0., W=W,
                regularize_full_psf=False, masks=~masks)

    optim = Optimizer(loss, parameters, method='adabelief')
    best_fit, logL_best_fit, extra_fields, runtime = optim.minimize(
        max_iterations=1500, min_iterations=None,
        init_learning_rate=1e-3, schedule_learning_rate=True,
        restart_from_init=True, stop_at_loss_increase=False,
        progress_bar=True, return_param_history=True
    )

    kwargs_final = parameters.args2kwargs(best_fit)

    # not forgetting to convolve with a gaussian of fwhm 2.0
    # sigma = 2.0 / 2.355  # convert fwhm to sigma.
    # return gaussian_filter(model.get_narrow_psf(**kwargs_final, norm=True), sigma)
    # ACTUALLY, let's return the narrow PSF: our model fitting will start from gaussians of 2 pics,
    # which might make it easier to handle multiple filters or epochs.

    ###########################################################################################
    # book keeping
    narrowpsf = model.get_narrow_psf(**kwargs_final, norm=True)
    fullmodel = np.array([model.model(i, **kwargs_final) for i in range(stars.shape[0])])
    ###########################################################################################

    return narrowpsf, fullmodel, L1 + optim.loss_history


def download_and_extract(ra, dec, workdir, survey='legacysurvey'):
    """
    This is a procedure, more than an atomic function. We do the following:
     - query the region around ra, dec for gaia detections, looking for stars we can use to model the PSF
     - if nothing useful, query a bigger region
     - download the data from the survey provided as an argument
     - extract cutouts of the lens (assumed to be at the provided ra,dec) and of the PSF stars
     - saves the cutouts to a file, returns both the path to the downloaded fits file and to the cutouts.

    :param ra: float, degrees
    :param dec: float, degrees
    :param workdir: string or Path, where are we working at?
    :param survey: from which survey shoulde get the imaging data?
    :return:
    """
    if survey not in config.supported_surveys:
        raise AssertionError(f"Don't know how to download from with this survey: {survey}")

    workdir = Path(workdir)

    # Very early on, we are going to check whether the data has already been downloaded and the cutouts extracted.
    # we will continue only provided that at least one of the two is missing.
    filename = f"cutouts_{survey}_{get_J2000_name(ra, dec)}.fits"

    workdir = Path(workdir)
    workdir.mkdir(exist_ok=True)

    savepath_fits = workdir / filename
    savepath_cutouts = workdir / (filename.replace('.fits', '_cutouts.h5'))
    if savepath_cutouts.exists() and savepath_fits.exists():
        return savepath_fits, savepath_cutouts

    # ok, now we can proceed.
    # downloading the images
    # try first with a "small" field (100 arcsec)
    fieldsize = 100
    goodstars = get_similar_stars(ra, dec, fieldsize/2)
    # if not, try making it bigger:
    if len(goodstars[0]) < 1:
        fieldsize = 200
        goodstars = get_similar_stars(ra, dec, fieldsize/2)
    # at this point, if still nothing we give up ...
    if len(goodstars[0]) < 1:
        raise RuntimeError("Really cannot find stars around {(ra, dec)} ...")

    savepath_fits = get_cutouts_file(ra, dec, fieldsize, downloaddir=workdir, survey=survey,
                                     filename=savepath_fits.name)

    # extracting the cutouts
    cutouts = {}
    names = 'abcde'  # no need for more than 5 stars, eva'
    for rastar, decstar, name in zip(*goodstars, names):
        cutouts[name] = extract_stamps(savepath_fits, rastar, decstar, survey, cutout_size=10)
        print(name)
    # also, let's get the lens!
    cutoutslens = extract_stamps(savepath_fits, ra, dec, survey, cutout_size=10)
    # todo deal with the lens, let's see once we have the PSF

    # cutouts: for each star, we have a dictionary of bands, the values of which are tuples
    # (star for each image, noisemap for each image)
    # we need to transform that into
    # {'band1': { 'image1': {'band1': np.array(star1,star2,...), np.array(noisemap1,noisemap2...)} ...}
    transformed_cutouts = {}

    for star, bands in cutouts.items():
        for band, (array1, array2) in bands.items():
            image_count = array1.shape[0]

            if band not in transformed_cutouts:
                transformed_cutouts[band] = {}

            for i in range(image_count):
                key = str(i)
                star = array1[i]
                noise = array2[i]
                if key not in transformed_cutouts[band]:
                    transformed_cutouts[band][key] = {}

                if 'stars' not in transformed_cutouts[band][key]:
                    transformed_cutouts[band][key]['stars'] = [star]
                else:
                    transformed_cutouts[band][key]['stars'].append(star)
                if 'noise' not in transformed_cutouts[band][key]:
                    transformed_cutouts[band][key]['noise'] = [noise]
                else:
                    transformed_cutouts[band][key]['noise'].append(noise)

    # once this is done, go back through each band, image and type of data to
    # make them numpy arrays ...sigh
    for band, banddata in transformed_cutouts.items():
        for imageindex, objects in banddata.items():
            for key, array in objects.items():
                objects[key] = np.array(array)

    # amazing, let's save it!
    # return transformed_cutouts
    save_dict_to_hdf5(savepath_cutouts, transformed_cutouts)
    return savepath_fits, savepath_cutouts


if __name__ == "__main__":
    RA, DEC = 320.6075, -16.357
    ff = download_and_extract(RA, DEC, workdir='/tmp', survey='panstarrs')
    estimate_psf_from_extracted_h5(ff[1])
