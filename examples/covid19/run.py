import argparse
import numpy as np

from jax.random import PRNGKey
from jax.experimental.optimizers import exponential_decay

import numpyro

from age_model import model

from numpyro.distributions import constraints, biject_to
from numpyro.infer import MCMC, NUTS, SVI, BarkerMH, init_to_value, autoguide, Trace_ELBO

from data import get_data, transform_data, generate_init_values
import pickle


def main(args):
    print(args)
    data = get_data()
    transformed_data = transform_data(data)

    print("M = {}  N0 = {}  N2 = {}  A = {}  SI_CUT = {}".format(data['M'], data['N0'], data['N2'], data['A'], data['SI_CUT']))

    if args.mode == 'mcmc':
        rng_key = PRNGKey(0)
        auto_scale, init_params = pickle.load(open("svi.pkl", "rb"))

        if args.mass_init == 'svi':
            auto_scale *= args.scale
        else:
            auto_scale = np.ones(auto_scale.shape)

        kernel = NUTS(model, step_size=args.step_size, adapt_step_size=True,
                      max_tree_depth=args.mtd, target_accept_prob=0.95)
        mcmc = MCMC(kernel, num_warmup=args.num_warmup, num_samples=args.num_samples + args.num_warmup,
                    num_chains=args.num_chains, progress_bar=True)
        mcmc._compile(rng_key, init_params=init_params, data=transformed_data)
        init_state = mcmc.last_state
        mcmc.post_warmup_state = init_state._replace(adapt_state=init_state.adapt_state._replace(
                                                     inverse_mass_matrix=auto_scale ** 2, mass_matrix_sqrt=1 / auto_scale))
        mcmc.run(rng_key, transformed_data)
        samples = mcmc.get_samples()
        mcmc.print_summary()

        final_inverse_mm = mcmc.last_state.adapt_state.inverse_mass_matrix
        final_scale = np.sqrt(final_inverse_mm)

        ratio = auto_scale / final_scale
        q03, q50, q97 = np.percentile(ratio, [3.0, 50.0, 97.0])
        print("[auto_scale / final_scale ratio] (min/mean/max):  %.2e %.2e %.2e" % (np.min(ratio),
                                                                                    np.mean(ratio), np.max(ratio)))
        print("[auto_scale / final_scale ratio] (q03/q50/q97):   %.2e %.2e %.2e" % (q03, q50, q97))

        f = 'samples.ns_nw_{}_{}.scale_{:.2f}.ss_{:.5f}.mtd_{}.{}.pkl'.format(args.num_samples,
                                                                              args.num_warmup, args.scale,
                                                                              args.step_size, args.mtd, args.mass_init)
        with open(f, "wb") as f:
            pickle.dump((auto_scale, init_params), f)

    elif args.mode == 'svi':
        init_loc_fn=init_to_value(values=generate_init_values(transformed_data, seed=3))
        guide = autoguide.AutoDiagonalNormal(model, init_loc_fn=init_loc_fn, init_scale=0.01)

        schedule = exponential_decay(0.01, args.num_steps, 0.01)
        svi = SVI(model, guide, numpyro.optim.ClippedAdam(schedule, clip_norm=0.1), Trace_ELBO())
        svi_result = svi.run(PRNGKey(0), args.num_steps, transformed_data)
        auto_scale = svi_result.params["auto_scale"]
        init_params = guide._unpack_latent(svi_result.params["auto_loc"])

        print("auto_scale", np.min(auto_scale), np.mean(auto_scale), np.max(auto_scale))

        with open('svi.pkl', "wb") as f:
            pickle.dump((auto_scale, init_params), f)

    elif args.mode == 'barker':
        rng_key = PRNGKey(0)
        auto_scale, init_params = pickle.load(open("svi.pkl", "rb"))

        kernel = BarkerMH(model, step_size=args.step_size)
        mcmc = MCMC(kernel, num_warmup=args.num_warmup, num_samples=args.num_samples + args.num_warmup,
                    num_chains=args.num_chains, progress_bar=True)
        mcmc._compile(rng_key, init_params=init_params, data=transformed_data)
        init_state = mcmc.last_state
        mcmc.post_warmup_state = init_state._replace(adapt_state=init_state.adapt_state._replace(
                                                     inverse_mass_matrix=auto_scale ** 2, mass_matrix_sqrt=1 / auto_scale))
        mcmc.run(rng_key, transformed_data)
        samples = mcmc.get_samples()
        mcmc.print_summary()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Covid Age Model")
    parser.add_argument("-n", "--num-samples", nargs="?", default=100, type=int)
    parser.add_argument("--num-warmup", default=200, type=int)
    parser.add_argument("--mode", default='mcmc', type=str, choices=['mcmc', 'svi'])
    parser.add_argument("--device", default='cpu', type=str, choices=['cpu', 'gpu'])
    parser.add_argument("--num-chains", default=1, type=int)
    parser.add_argument("--num-steps", default=3200, type=int)
    parser.add_argument("--scale", default=1.0, type=float)
    parser.add_argument("--step-size", default=0.02, type=float)
    parser.add_argument("--mtd", default=15, type=int)
    parser.add_argument("--mass-init", default='default', type=str, choices=['svi', 'default'])
    args = parser.parse_args()

    numpyro.enable_x64()
    numpyro.set_platform(args.device)
    numpyro.set_host_device_count(args.num_chains)

    main(args)
