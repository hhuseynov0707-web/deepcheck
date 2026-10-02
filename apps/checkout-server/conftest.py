import os

# Set before main is imported by any test module. main reads the environment
# at startup (lifespan), not at import, but a test that starts the app without
# going through its own fixture should still find a complete configuration.
os.environ.setdefault("CHECKOUT_MERCHANT_ID", "techstore")
os.environ.setdefault("CHECKOUT_MERCHANT_KEY", "test-merchant-key-not-for-production")
os.environ.setdefault("CHECKOUT_CUSTOMER_REF_KEY", "test-customer-ref-key-not-for-production")
os.environ.setdefault("DEEPCHECK_CORE_URL", "http://core.test")
