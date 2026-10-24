"""pytest setup: make the middleware modules importable as top-level modules
(they import each other as `from mav_node import ...`)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
