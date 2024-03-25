from getConfig import getFetchMethod
from sys import argv

try:
    command = argv[1]
except IndexError:
    command = None
method = getFetchMethod()
if(method == "rss"):
    import getRSS
    getRSS.main(command)
elif(method == "json"):
    import getJson
    getJson.main(command)
else:
    print("Invalid method: {0}".format(method))