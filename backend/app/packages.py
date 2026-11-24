"""Shared import/distribution identity, without importing tutorial packages."""

import re
import sys
from collections.abc import Collection, Iterable

# Import name -> PyPI name, for packages whose import name differs from the install name.
# Dotted keys match a module path prefix. Extend this dict when a tutorial needs more.
IMPORT_TO_PYPI: dict[str, str] = {
    "sklearn": "scikit-learn",
    "cv2": "opencv-python",
    "PIL": "pillow",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "skimage": "scikit-image",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "attr": "attrs",
    "jwt": "pyjwt",
    "serial": "pyserial",
    "usb": "pyusb",
    "OpenSSL": "pyopenssl",
    "Crypto": "pycryptodome",
    "Cryptodome": "pycryptodomex",
    "git": "gitpython",
    "magic": "python-magic",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "fitz": "pymupdf",
    "mpl_toolkits": "matplotlib",
    "pylab": "matplotlib",
    "tensorflow_hub": "tensorflow-hub",
    "tensorflow_datasets": "tensorflow-datasets",
    "tensorflow_probability": "tensorflow-probability",
    "tensorflow_text": "tensorflow-text",
    "tf_keras": "tf-keras",
    "google.generativeai": "google-generativeai",
    "googleapiclient": "google-api-python-client",
    "telegram": "python-telegram-bot",
    "slugify": "python-slugify",
    "socks": "pysocks",
    "zmq": "pyzmq",
    "sentence_transformers": "sentence-transformers",
    "umap": "umap-learn",
    "faiss": "faiss-cpu",
    "langchain_openai": "langchain-openai",
    "langchain_community": "langchain-community",
    "langchain_core": "langchain-core",
    "langchain_text_splitters": "langchain-text-splitters",
    "langchain_anthropic": "langchain-anthropic",
    "langchain_google_genai": "langchain-google-genai",
    "llama_index": "llama-index",
    "pydantic_settings": "pydantic-settings",
    "win32api": "pywin32",
    "win32com": "pywin32",
    "speech_recognition": "speechrecognition",
    "wx": "wxpython",
    "gi": "pygobject",
    "Xlib": "python-xlib",
    "psycopg2": "psycopg2-binary",
    "MySQLdb": "mysqlclient",
    "ldap": "python-ldap",
    "Bio": "biopython",
    "kafka": "kafka-python",
    "nacl": "pynacl",
    "pkg_resources": "setuptools",
    "IPython": "ipython",
    "jose": "python-jose",
    "multipart": "python-multipart",
    "socketio": "python-socketio",
    "engineio": "python-engineio",
    "decouple": "python-decouple",
    "cairo": "pycairo",
    "OpenGL": "pyopengl",
}


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dist_for_import(import_name: str) -> str:
    parts = import_name.split(".")
    for end in range(len(parts), 0, -1):
        mapped = IMPORT_TO_PYPI.get(".".join(parts[:end]))
        if mapped:
            return normalize(mapped)
    return normalize(parts[0])


def guessed_distributions(
    import_names: Iterable[str], explicit_installs: Collection[str] = ()
) -> set[str]:
    """Distributions inferred only from an unmapped import, without an install line."""
    guessed: set[str] = set()
    known = {normalize(name) for name in explicit_installs}
    for module in import_names:
        if is_stdlib(module):
            continue
        parts = module.split(".")
        distribution = dist_for_import(module)
        if any(".".join(parts[:end]) in IMPORT_TO_PYPI for end in range(1, len(parts) + 1)):
            known.add(distribution)
        else:
            guessed.add(distribution)
    return guessed - known


def imports_for_dist(dist_name: str) -> set[str]:
    normalized = normalize(dist_name)
    return {
        name.split(".")[0] for name, dist in IMPORT_TO_PYPI.items() if normalize(dist) == normalized
    } | {normalized.replace("-", "_")}


def is_stdlib(import_name: str) -> bool:
    return import_name.split(".")[0] in sys.stdlib_module_names


def same_dist(a: str, b: str) -> bool:
    return normalize(a) == normalize(b)
