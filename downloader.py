import yt_dlp

def download_youtube_video(video_url):
    ydl_opts = {
        # 'bestvideo+bestaudio/best'
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': '%(title)s.%(ext)s',
    }
    
    print("Начинаю скачивание в лучшем качестве... Пожалуйста, подождите.")
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        print("\nВидео успешно скачано!")
    except Exception as e:
        print(f"\nПроизошла ошибка: {e}")

if __name__ == "__main__":
    url = input("Вставьте ссылку на YouTube видео: ")
    download_youtube_video(url)