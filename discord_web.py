
import requests
#import json
import sys
from discord_webhook import DiscordWebhook, DiscordEmbed
from config import webhook_url 

def send_webhook(url, id="Unknown", status="error"):
    webhook = DiscordWebhook(url, rate_limit_retry=True)
    if(status == "starting"):
        title="Starting"
        color="fc8803"
        embed = DiscordEmbed(title, description="Starting holo-downloader", color=color)
        webhook.add_embed(embed)
        webhook_response = webhook.execute()
        return
    elif(status=="membership-error"):
        title = "Membership error"
        color="ff0000"
        embed = DiscordEmbed(title, description="Error checking membership streams for [{0}](www.youtube.com/channel/{0}). Check cookies".format(id), color=color)
        embed.set_author(name=id, url='www.youtube.com/channel/{0}'.format(id))
        #embed.set_thumbnail(url=data.get('thumbnail_url'))
        webhook.add_embed(embed)
        webhook_response = webhook.execute()
        return
    
    response = requests.get("https://www.youtube.com/oembed?format=json&url=https://www.youtube.com/watch?v={0}".format(id))
    
    color="03b2f8"   

    # create embed object for webhook
    # you can set the color as a decimal (color=242424) or hex (color="03b2f8") number
    match status:
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

    # Check if the request was successful (status code 200)
    if response.status_code == 200:
        # Parse the JSON data from the response
        data = response.json()

        embed = DiscordEmbed(title, description="[{0}](https://youtu.be/{1})".format(data.get('title'),id), color=color)

        embed.set_author(name=data.get('author_name'), url=data.get('author_url'))

        embed.set_thumbnail(url=data.get('thumbnail_url'))

    else:                
        embed = DiscordEmbed(title, description="Video https://youtu.be/{0} is not accessible, perhaps it has been privated".format(id), color=color)

    # add embed object to webhook
    webhook.add_embed(embed)

    webhook_response = webhook.execute()

def main(id, status):
    if webhook_url is not None:
        send_webhook(webhook_url, id, status)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
