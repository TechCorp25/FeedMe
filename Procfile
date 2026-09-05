# Read by Railway (and any Procfile-aware host). Render uses the
# startCommand in render.yaml. Both bind the port the platform assigns.
web: gunicorn --bind "0.0.0.0:${PORT:-5000}" --workers "${WEB_CONCURRENCY:-2}" --timeout 60 --access-logfile - wsgi:app
