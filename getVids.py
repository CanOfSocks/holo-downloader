#!/usr/local/bin/python
from getConfig import getFetchMethod
from sys import argv
command = None
try:
    command = argv[1]
except IndexError:
    command = None
method = getFetchMethod()
if(method == "ytdlp"):
    import getYTDLP
    getYTDLP.main(command)
elif(method == "json"):
    import getJson
    getJson.main(command)
else:
    print("Invalid method: {0}".format(method))