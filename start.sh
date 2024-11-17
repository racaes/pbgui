#!/usr/bin/bash
venv=home/tdb/git/pbgui/venv      #Path to python venv
pbgui=home/tdb/git/pbgui          #path to pbgui installation

source ${venv}/bin/activate
cd ${pbgui}
python PBRun.py &
python PBRemote.py &
python PBCoinData.py &
python PBStat.py &
python PBData.py &
