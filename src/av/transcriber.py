# Install the requests package by executing the command "pip install requests"

import requests
import time

from secrets import assembly_ai_auth_key

base_url = "https://api.assemblyai.com"
input_filepath = "/Users/gautam/Movies/4K Video Downloader+/Audio/City Council Meeting 2025-10-15 - City of Clayton.m4a"
output_filepath = "/Users/gautam/Movies/4K Video Downloader+/Transcripts/City Council Meeting 2025-10-15 - City of Clayton - Utterances.txt"
headers = {
    "authorization": assembly_ai_auth_key
}
# You can upload a local file using the following code
with open("/Users/gautam/Movies/4K Video Downloader+/Audio/City Council Meeting 2025-10-15 - City of Clayton.m4a", "rb") as f:
  response = requests.post(base_url + "/v2/upload",
                          headers=headers,
                          data=f)

audio_url = response.json()["upload_url"]

data = {
    "audio_url": audio_url,
    "language_detection": True,
    # Uses universal-3-pro for en, es, de, fr, it, pt. Else uses universal-2 for support across all other languages
    "speech_models": ["universal-3-pro", "universal-2"],
    "speaker_labels": True,
}

url = base_url + "/v2/transcript"
response = requests.post(url, json=data, headers=headers)

transcript_id = response.json()['id']
polling_endpoint = base_url + "/v2/transcript/" + transcript_id

while True:
  transcription_result = requests.get(polling_endpoint, headers=headers).json()
  transcript_text = transcription_result['text']

  if transcription_result['status'] == 'completed':
    u = open(output_filepath, 'w')
    for utterance in transcription_result['utterances']:
        u.write(f"Speaker {utterance['speaker']}:\n{utterance['text']}\n\n")
    u.close()
    break

  elif transcription_result['status'] == 'error':
    raise RuntimeError(f"Transcription failed: {transcription_result['error']}")

  else:
    time.sleep(30)
