#!/usr/local/bin/python
from getConfig import getFetchMethod
from sys import argv
command = None
unarchived = False
try:
    command = argv[1]
except IndexError:
    command = None
try:
    if argv[2] == "unarchived":
        unarchived = True
except IndexError:
    pass    

method = getFetchMethod()
if(method == "ytdlp"):
    import getYTDLP
    getYTDLP.main(command,unarchived=unarchived)
elif(method == "json"):
    import getJson
    getJson.main(command,unarchived=unarchived)
else:
    print("Invalid method: {0}".format(method))