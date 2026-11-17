#!/bin/bash
cd ~/Projects/365-backend-python
source venv/bin/activate
# source ~/.transcripts_env
# : "${DB_NAME:?DB_NAME is not set - refusing to start on an empty database}"
# sudo service postgresql start
# python3 manage.py migrate
python main.py runserver