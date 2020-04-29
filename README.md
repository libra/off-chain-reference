# off-chain-api

Off-Chain API: Supports exchanging KYC data and attestation of KYC data between VASPs.
It uses standard transports and technologies such as HTTP, JSON and TLS to make
compliant implementations easy and flexible.

# Installation

To install the API, activate the Python virtual environment you use, and then execute:

    git clone https://github.com/calibra/off-chain-api.git
    cd off-chain-api
    pip install .

If you plan to do development for the Off-Chain API consider installing in _develop_ editable mode:

    pip install -e .

You can also use `tox` to run all the tests and build the documentation:

    pip install tox
    tox
    tox -e docs

This should create a number of resources:

* It will run all `pytest` tests and the local benchmark under coverage.
* It will create HTML *source code coverage reports* under `htmlcov/index.html`.
* It will build the documentation under `docs/_build/html/index.html`.

The index of the documentation is a very good place to start to learn more.
