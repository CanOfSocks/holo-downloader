from sys import argv
import config
from pathlib import Path,PurePath

#For general cookies
def getCookiesOptions():
    out = ""
    try:
        cookies = config.cookies_file
    except AttributeError:
        cookies = None
        
    if cookies is not None:
        out += " --cookies '{0}'".format(config.cookies_file)
        
    return out

#For waiter
def get_ytdlp():
    out = ""
    #Cookies
    out += getCookiesOptions()
        
    # Output file(s)
    try:
        output_folder = config.output_folder
    except AttributeError:
        output_folder = "%(fulltitle)s"
    # Output folder is empty, add default
    if not output_folder:
        output_folder = "%(fulltitle)s"
        
    # duplicate path if depth is 1 (or less)
    print(len(Path(output_folder).parents))
    if(len(Path(output_folder).parents) <= 1):
        output_folder = str(PurePath(output_folder,output_folder))
    
    out += " -o {0}".format(output_folder)
    
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
    

#Info to download
def getInfo():
    out = ""
    #Check if video only
    
    if vid_Only():
        return out
    
    try:
        thumbnail = config.thumbnail
    except AttributeError:
        thumbnail = True
    if thumbnail:
        out += " --write-thumbnail --convert-thumbnails png"
        
    try:
        info_json = config.info_json
    except AttributeError:
        info_json = True
    if info_json:
        out += " --write-info-json"
        
    try:
        description = config.description
    except AttributeError:
        description = True
    if description:
        out += " --write-description"
        
    out += getCookiesOptions()
    
    return out
        
def getMux():
    mux_file = True
    try:
        mux_file = config.mux_file
    except AttributeError:
        pass
    return mux_file
    
def getYtarchiveOptions():
    out = ""
    try:
        ytarchive_options = config.ytarchive_options
    except AttributeError:
        ytarchive_options = None
        
    out += " " + ytarchive_options
    
    try:
        download_threads = config.download_threads
    except AttributeError:
        download_threads = 4
        
    out += " --threads " + str(download_threads)
    
    #Embed thumbnail?
    try:
        thumbnail = config.thumbnail
    except AttributeError:
        thumbnail = True
    if thumbnail:
        out += " -t"
    
    if not getMux():
        out += " --write-mux-file"
       
    out += getCookiesOptions()
        
    return out   

def getQuality():
    out = "best"  
    try:
        out = str(config.video_quality)
    #Any issue, go to default of best
    except Exception: 
        pass
    return out

function = argv[1]

match function:
    case "cookies":
        print(getCookiesOptions())
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
        
    
    
