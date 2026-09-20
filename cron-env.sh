#!/bin/bash
cd /root/work-optimization
set -a; source ./.env; set +a
exec .venv/bin/python -m hh_applicant_tool "$@"
