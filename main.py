# 1. Kata 'async' sudah dihapus
@app.post("/download-clip")
def download_clip(req: ClipRequest, background_tasks: BackgroundTasks):
    unique_name = f"clip_{uuid.uuid4().hex[:8]}.mp4"
    
    try:
        start_sec = parse_time(req.start)
        end_sec = parse_time(req.end)
        durasi = end_sec - start_sec

        # 3. Validasi tambahan untuk melindungi server
        if durasi < 1:
            raise HTTPException(status_code=400, detail="Durasi terlalu pendek")
        if durasi > 180:
            raise HTTPException(status_code=400, detail="Maksimal durasi klip adalah 3 menit (180 detik)")

        print(f"Processing: {req.url} ({start_sec}s - {end_sec}s)")

        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            
            # 2. Pengaman tambahan agar hasil gabungan FFmpeg mutlak menjadi .mp4
            'merge_output_format': 'mp4',
            
            'outtmpl': unique_name,
            'download_ranges': lambda info, ydl_ops: [{"start_time": start_sec, "end_time": end_sec}],
            'force_ipv4': True,
            'noplaylist': True,
            'quiet': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web']
                }
            }
        }

        # Eksekusi Download
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([req.url])

        if not os.path.exists(unique_name):
            raise HTTPException(status_code=500, detail="Gagal memproses file klip")

        # Kirim file ke user & hapus setelah terkirim (background task)
        background_tasks.add_task(remove_file, unique_name)
        
        return FileResponse(
            path=unique_name, 
            filename="my_clip.mp4", 
            media_type="video/mp4"
        )

    except Exception as e:
        # Bersihkan jika error di tengah jalan
        if os.path.exists(unique_name):
            os.remove(unique_name)
        print(f"Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))