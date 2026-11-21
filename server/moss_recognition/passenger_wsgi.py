"""Passenger/WSGI entry point for a dedicated cPanel Python app."""
from server import create_app

application = create_app()
