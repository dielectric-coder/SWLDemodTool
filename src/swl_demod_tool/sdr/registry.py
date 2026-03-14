"""SDR backend registry and factory."""

import importlib

# Each entry maps a CLI name to (module_path, class_name, description)
SDR_BACKENDS = {
    "elad-fdmduo": (
        "swl_demod_tool.sdr.elad_fdmduo",
        "EladFDMDuoSource",
        "Elad FDM-DUO via TCP IQ + CAT server",
    ),
    # Future backends:
    # "soapy": ("swl_demod_tool.sdr.soapy", "SoapySource", "SoapySDR devices"),
}

DEFAULT_BACKEND = "elad-fdmduo"


def list_backends():
    """Return list of (name, description) for all registered backends."""
    return [(name, entry[2]) for name, entry in SDR_BACKENDS.items()]


def create_sdr_source(backend_name, config, cli_args):
    """Create an SDRSource instance for the given backend name.

    Uses lazy imports so backend-specific dependencies are only loaded
    when that backend is actually selected.
    """
    if backend_name not in SDR_BACKENDS:
        available = ", ".join(SDR_BACKENDS)
        raise ValueError(f"Unknown SDR backend '{backend_name}'. Available: {available}")

    module_path, class_name, _ = SDR_BACKENDS[backend_name]
    mod = importlib.import_module(module_path)
    cls = getattr(mod, class_name)

    # Backend-specific construction
    if backend_name == "elad-fdmduo":
        host = getattr(cli_args, "host", None) or config.get("server", "host")
        iq_port = getattr(cli_args, "iq_port", None) or config.getint("server", "iq_port")
        cat_port = getattr(cli_args, "cat_port", None) or config.getint("server", "cat_port")
        return cls(host=host, iq_port=iq_port, cat_port=cat_port)

    # Generic fallback for future backends
    return cls()
