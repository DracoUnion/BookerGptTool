rm -rf dist
python -m build
twine upload dist/* -u $(pip config get pypi.username) -p $(pip config get pypi.password)