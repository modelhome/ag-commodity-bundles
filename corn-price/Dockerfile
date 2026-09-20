# to build and run from your local machine (build context is this corn-price/
# folder, which is also what Model Home uses when the model is added from
# github.com/modelhome/ag-commodity-bundles/tree/main/corn-price):
#   cd corn-price && docker build -t ag-commodity-corn-price:local .
#   docker run --rm --network none ag-commodity-corn-price:local  # uses sample_input.json
#   mkdir -p run && cp sample_input.json run/corn_yield_snapshot.json
#   docker run --rm --network none -v "$PWD/run:/run" ag-commodity-corn-price:local \
#       /run/corn_yield_snapshot.json /run/corn_price_regions.output.json \
#       > run/corn_price_impact.output.json
# The model makes no network calls at run time: --network none is the test.

FROM python:3.12-slim

WORKDIR /app

# No pip install layer. The whole model is weighted sums, an empirical quantile
# and one committed coefficient, so it runs on the standard library alone --
# there is nothing here that numpy or pandas would make clearer or faster, and
# the repo's rule is that a dependency needs a reason. The image is the slim
# Python base and nothing else.

# The committed tables are part of the model, not inputs: the production weights
# and irrigation shares, the observed 1995-2024 state yield history the ranks are
# mapped onto, the national balance sheet the reference price and carry-in come
# from, and the fitted transmission. The four build_*.py scripts and
# check_price.py are development tools and are deliberately not copied in -- they
# need network and, between them, a 1.4 GB of USDA export that has no business in
# a runtime image.
COPY runner.py \
     production_weights.csv \
     production_weights.meta.json \
     yield_history.csv \
     yield_history.meta.json \
     price_history.csv \
     price_history.meta.json \
     transmission.json \
     sample_input.json \
     ./

ENTRYPOINT ["python", "runner.py"]

# irl the input/output paths come from the Modelfile; for local testing we
# default to the bundled sample input.
CMD ["sample_input.json"]
