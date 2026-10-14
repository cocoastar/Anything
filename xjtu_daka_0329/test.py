from openai import OpenAI
import base64

client = OpenAI(
    base_url="https://api.vectorengine.ai/v1",
    api_key="sk-QlJROKYSmbQijphnUPtoYArRBZLxQ0k8Th1XFp7M5c3kEOlc"
)
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')
        
base64_image= encode_image("/data1/jtc/xjtu_daka/uploads/canteen/3bbca88ff94042c3aa0e97ec9b294bcb.png")

response = client.chat.completions.create(
  model="gpt-4o",
  messages=[
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "What’s in this image?"},
        {
          "type": "image_url",
          "image_url": {
            "url": f"data:image/png;base64,{base64_image}",
          },
        },
      ],
    }
  ],
  max_tokens=300,
)

print(response.choices[0])