from sys import argv
import config
from pathlib import Path,PurePath

#For general cookies
#def getCookiesOptions():
def getCookiesFile():
    out = ""
    try:
        cookies = config.cookies_file
    except AttributeError:
        cookies = None
        
    if cookies is not None:
#        out += " --cookies {0}".format(config.cookies_file)

        # Get Cookies path with conversion for specific OS
        out += str(Path(config.cookies_file))
    return out

#For waiter
def get_ytdlp():
    out = ""
    #Cookies
#    out += getCookiesOptions()
        
    # Output file(s)
    try:
        output_folder = config.output_folder
    except AttributeError:
        output_folder = "%(fulltitle)s"
    # Output folder is empty, add default
    if not output_folder:
        output_folder = "%(fulltitle)s"
        
    # duplicate path if depth is 1 (or less)
    if(len(Path(output_folder).parents) <= 1):
        output_folder = str(PurePath(output_folder,output_folder))
    
    #out += " -o {0}".format(output_folder)
    str(Path(config.cookies_file))
    out += str(Path(output_folder))
    return out

#Check if vidOnly
def vid_Only():
    try:
        video_only = config.video_only
    except AttributeError:
        video_only = False
    return video_only
    

def getChat():
    #Default to state of video_only variable in config
    chat = not vid_Only()
    if chat:
        try:
            chat = config.download_chat
        except AttributeError:
            pass
        
    return chat
    

def getThumbnail():
    try:
        thumbnail = config.thumbnail
    except AttributeError:
        thumbnail = True
    return thumbnail
def getDescription():
    try:
        description = config.description
    except AttributeError:
        description = True
    return description
def getInfoJson():
    try:
        info_json = config.info_json
    except AttributeError:
        info_json = True
    return info_json
    
#Info to download
def getInfo():
    out = []
    #Check if video only
    
    if vid_Only():
        return out    

    if getThumbnail():
        out += ["--write-thumbnail", "--convert-thumbnails", "png"]
        
    if getInfoJson():
        out += ["--write-info-json"]
        

    if getDescription():
        out += ["--write-description"]
        
    #out += getCookiesOptions()
    
    return out
        
def getMux():
    mux_file = True
    try:
        mux_file = config.mux_file
    except AttributeError:
        pass
    return mux_file
    
def getYtarchiveOptions():
    out = []
    try:
        ytarchive_options = config.ytarchive_options
    except AttributeError:
        ytarchive_options = None
        
    out += ytarchive_options.split(' ')
    
    try:
        download_threads = config.download_threads
    except AttributeError:
        download_threads = 4
        
    out += ["--threads", str(download_threads)]
    
    #Embed thumbnail?
    try:
        thumbnail = config.thumbnail
    except AttributeError:
        thumbnail = True
    if thumbnail:
        out += ["-t"]
    
    if not getMux():
        out += ["--write-mux-file"]
       
    #out += getCookiesOptions()
        
    return out   

def getQuality():
    out = "best"  
    try:
        out = str(config.video_quality)
    #Any issue, go to default of best
    except Exception: 
        pass
    return out

def getTempFolder():
    out = "/app/temp/"  
    try:
        out = str(config.tempdir)
    #Any issue, go to default of best
    except Exception: 
        pass
    return out

def getDoneFolder():
    out = "/app/Done/"   
    try:
        out = str(config.donedir)
    #Any issue, go to default of best
    except Exception: 
        pass
    return out

def getTempOutputPath(output):
    return Path(getTempFolder()) / Path(output)

def getDoneOutputPath(output):
    return Path(getDoneFolder()) / Path(output)

def ytarchiveBuilder(id,output):
    out = ["ytarchive", "--error"]
    out += getYtarchiveOptions()
    cookies = getCookiesFile()
    if cookies:
        out += ["--cookies", cookies]
    outputFolder = getTempOutputPath(output)
    out += ["--output", str(outputFolder)]
    out.append("https://www.youtube.com/watch?v={0}".format(id))
    out.append(getQuality())
    return out

def getTorrent():
    getTorrent = True
    try:
        getTorrent = config.torrent
    except AttributeError:
        pass
    return getTorrent

def torrentBuilder(output, folder):
    options = ['py3createtorrent']
    try:
        options += config.torrentOptions
    except AttributeError:
        pass
    options += ['-o', "{0}.torrent".format(str(output))]
    options += [str(folder)]
    return options

def getLookAhead():
    return config.look_ahead

def membership_directory():
    output = None
    try:
        output = config.membersdir
    except AttributeError:
        pass
    return output   

def getMembershipOutputPath(output):
    return Path(membership_directory()) / Path(output)

def getCommunityTabArchive():
    output = None
    try:
        output = config.comm_tab_archive
    except AttributeError:
        pass
    return output 

def getCommunityTabDirectory():
    output = None
    try:
        output = config.communitydir
    except AttributeError:
        pass
    return output 

def main(function):
    match function:
        case "cookies":
            #print(getCookiesOptions())
            print(getCookiesFile())
        case "yt-dlp_options":
            print(get_ytdlp())
        case "info_options":
            print(getInfo())
        case "get_chat":
            print(getChat())
        case "ytarchive_options":
            print(getYtarchiveOptions())
        case "mux_file":
            print(getMux())
        case "quality":
            print(getQuality())
        
def getFetchMethod():
    output = "rss"
    try:
        output = config.fetch_method
        return output
    except AttributeError:
        pass
    return output  

if __name__ == "__main__":
    main(argv[1])



    
    
