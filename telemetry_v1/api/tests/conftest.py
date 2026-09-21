import os
import tempfile

# Never download the geolocation database during tests.
os.environ["GEOIP_AUTO_UPDATE"] = "false"
os.environ["GEOIP_DIR"] = tempfile.mkdtemp(prefix="geoip-test-")
