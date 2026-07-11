import os
import sys
import time
import requests

def post_to_instagram():
    instagram_id = os.environ.get("INSTAGRAM_BUSINESS_ACCOUNT_ID")
    access_token = os.environ.get("FACEBOOK_ACCESS_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")

    if not instagram_id or not access_token:
        print("[WARNING] INSTAGRAM_BUSINESS_ACCOUNT_ID or FACEBOOK_ACCESS_TOKEN is not configured.")
        print("Skipping automatic Instagram publish. Set up these secrets in your GitHub repository.")
        sys.exit(0)

    if not repo:
        print("[ERROR] GITHUB_REPOSITORY environment variable is missing. This script should run inside GitHub Actions.")
        sys.exit(1)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    root_dir = os.path.dirname(script_dir)
    
    caption_path = os.path.join(root_dir, "website", "public", "current_caption.txt")
    if not os.path.exists(caption_path):
        print(f"[ERROR] Caption file not found at {caption_path}")
        sys.exit(1)

    with open(caption_path, "r", encoding="utf-8") as f:
        caption = f.read()

    # Construct the public URL of the generated image pushed to the main branch
    image_url = f"https://raw.githubusercontent.com/{repo}/main/website/public/current_post.png"
    print(f"Submitting post image URL: {image_url}")

    # 1. Create Media Container on Instagram
    container_url = f"https://graph.facebook.com/v17.0/{instagram_id}/media"
    payload = {
        "image_url": image_url,
        "caption": caption,
        "access_token": access_token
    }
    
    try:
        print("Creating media container on Instagram...")
        r = requests.post(container_url, data=payload, timeout=20)
        res = r.json()
        if r.status_code != 200 or "id" not in res:
            print(f"[ERROR] Failed to create media container: {res}")
            sys.exit(1)
            
        creation_id = res["id"]
        print(f"Container created successfully. Creation ID: {creation_id}")
        
        # Wait for Instagram to process the image
        print("Waiting 15 seconds for Instagram to download and process the image...")
        time.sleep(15)
        
        # 2. Publish the Container
        publish_url = f"https://graph.facebook.com/v17.0/{instagram_id}/media_publish"
        publish_payload = {
            "creation_id": creation_id,
            "access_token": access_token
        }
        
        print("Publishing container to Instagram feed...")
        r_pub = requests.post(publish_url, data=publish_payload, timeout=20)
        res_pub = r_pub.json()
        
        if r_pub.status_code != 200 or "id" not in res_pub:
            print(f"[ERROR] Failed to publish post: {res_pub}")
            sys.exit(1)
            
        post_id = res_pub["id"]
        print(f"★ Success! Post published on Instagram. Post ID: {post_id}")
        
    except Exception as e:
        print(f"[ERROR] Instagram API connection failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    post_to_instagram()
