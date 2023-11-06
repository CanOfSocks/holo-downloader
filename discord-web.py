import requests
#import json
import sys
from discord_webhook import DiscordWebhook, DiscordEmbed
from config import webhook_url 

def send_webhook(url, type, id="Unknown"):
    response = requests.get("https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={0}".format(id))

    # Check if the request was successful (status code 200)
    if response.status_code == 200:
        # Parse the JSON data from the response
        data = response.json()
        color="03b2f8"

        webhook = DiscordWebhook(url, rate_limit_retry=True)

        # create embed object for webhook
        # you can set the color as a decimal (color=242424) or hex (color="03b2f8") number
        match type:
            case "recording":
                title = "Recording"
                color="0011ff"
            case "waiting":
                title = "Waiting"
                color="ffff00"
            case "error":
                title = "Error"
                color="ff0000"
            case "done":
                title = "Done"
                color="00ff00"



        author = [""]


        embed = DiscordEmbed(title, description="[{0}](https://youtu.be/{1})".format(data.get('title'),id), color=color)

        embed.set_author(name=data.get('author_name'), url=data.get('author_url'))

        embed.set_thumbnail(url=data.get('thumbnail_url'))

        # add embed object to webhook
        webhook.add_embed(embed)

        webhook_response = webhook.execute()
    else:
        webhook = DiscordWebhook(url, rate_limit_retry=True)
        match type:
            case "recording":
                title = "Recording"
                color="0011ff"
            case "waiting":
                title = "Waiting"
                color="ffff00"
            case "error":
                title = "Error"
                color="ff0000"
            case "done":
                title = "Done"
                color="00ff00"
                
        embed = DiscordEmbed(title, description="Video https://youtu.be/{1} is not accessible, perhaps it has been privated".format(data.get('title'),id), color=color)
        
        webhook.add_embed(embed)

        webhook_response = webhook.execute()

id = sys.argv[1]
status = sys.argv[2]

send_webhook(webhook_url, status, id)
