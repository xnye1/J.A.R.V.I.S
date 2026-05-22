import pyaudio
p = pyaudio.PyAudio()
print("\n=== [ 마이크 장치 목록 ] ===")
for i in range(p.get_device_count()):
    info = p.get_device_info_by_index(i)
    # 입력 채널이 있는 기기(마이크)만 출력
    if info.get('maxInputChannels') > 0:
        print(f"Index {i}: {info.get('name')}")
p.terminate()