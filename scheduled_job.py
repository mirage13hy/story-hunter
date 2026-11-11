"""
클라우드 정기 자동 실행 워커 (Scheduled Job)
PC가 꺼져 있어도 클라우드 환경(GitHub Actions, Cloud Run 등)에서 정기적으로 실행되어
새로운 장르/매체의 숨은 걸작을 자동 발굴하고 큐에 적재합니다.
"""

import os
import json
import random
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from pipeline import StoryHunterPipeline

# 순환 탐색 테마 풀 (다양하고 참신한 장르/매체)
THEME_POOL = [
    "상업적 성공은 못했으나 스토리가 경이로운 숨은 하이스트/케이퍼 걸작",
    "서부극 또는 개척 시대 배경의 치열한 복수극 및 인간 군상 명작",
    "폐쇄 공간(밀실/고립된 지역)에서 벌어지는 심리 스릴러와 반전 추리극",
    "거대한 음모와 정보전, 배신이 얽힌 냉전 스파이물 및 첩보 스릴러",
    "독특한 규칙이나 제약 조건이 걸려 있는 배틀로얄 또는 서바이벌 게임 스토리",
    "비정한 지하 범죄 조직과 공권력의 결탁을 다룬 하드보일드 누아르",
    "시간 루프 또는 기억 상실을 활용한 기발한 미스터리 서사",
    "상인/상단 간의 피 튀기는 경제 암투와 상권 쟁탈전을 다룬 시대극",
    "세계 각국의 신화/전설을 현대적으로 재해석한 기괴하고 매력적인 스토리",
    "레딧(Reddit) 미스터리 스레드나 컬트 만화/애니메이션 중 플롯이 파격적인 작품"
]

def run_scheduled_discovery(count: int = 3):
    print("=" * 60)
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 정기 자동 발굴 워커 시작")
    print("=" * 60)

    pipeline = StoryHunterPipeline(storage_dir="storage")
    
    # 테마 무작위 선정
    selected_theme = random.choice(THEME_POOL)
    print(f">> 이번 정기 발굴 테마: [{selected_theme}]")
    
    # 작품 발굴 실행
    works = pipeline.discover_works(theme_or_genre=selected_theme, count=count)
    print(f">> 발굴된 작품 수: {len(works)}건")

    # 대시보드에서 볼 수 있는 큐(Queue) 파일에 저장
    queue_file = Path("storage") / "discovered_queue.json"
    existing_queue = []
    if queue_file.exists():
        try:
            with open(queue_file, "r", encoding="utf-8") as f:
                existing_queue = json.load(f)
        except Exception:
            existing_queue = []

    # 중복 제외하고 큐의 앞쪽에 추가
    existing_titles = {w.get("title") for w in existing_queue}
    new_items = []
    for w in works:
        w["discovered_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        w["theme"] = selected_theme
        if w.get("title") not in existing_titles:
            new_items.append(w)
            print(f"   + 신규 발굴: {w.get('title')} ({w.get('year')}) - {w.get('media')}")

    updated_queue = new_items + existing_queue
    # 큐 최대 50개 유지
    updated_queue = updated_queue[:50]

    with open(queue_file, "w", encoding="utf-8") as f:
        json.dump(updated_queue, f, ensure_ascii=False, indent=2)

    print(f">> 발굴 큐 갱신 완료 (현재 대기 중인 추천작: {len(updated_queue)}개)")
    print("=" * 60)

if __name__ == "__main__":
    run_scheduled_discovery(count=3)
