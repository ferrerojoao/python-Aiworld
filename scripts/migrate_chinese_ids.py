"""一次性数据迁移：场景/NPC 键改为中文名，删除冗余字段。

- world.json: 删 default_durations
- scenes.json: id=中文名；删 name/open_hours/adjacent；aliases 去掉与 id 重复项
- npcs/*.json: id=中文名，删 name 字段，文件名改为 <id>.json
- saves/*/save.json: player_scene 映射到新场景 id；删 scene_name
- saves/*/events.jsonl: location/participants 中的旧英文 id 映射到中文
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "content"

# 迁移前 player_scene 特例：tianji2 无场景表，保持默认
SPECIAL_PLAYER_SCENE = {"shenshan": "大槐树"}


def migrate_world(wdir: Path) -> None:
    # --- world.json ---
    wj = wdir / "world.json"
    if wj.exists():
        meta = json.loads(wj.read_text("utf-8"))
        meta.pop("default_durations", None)
        wj.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", "utf-8")

    # --- scenes.json ---
    scene_map: dict[str, str] = {}
    sj = wdir / "scenes.json"
    if sj.exists():
        scenes = json.loads(sj.read_text("utf-8"))
        new_scenes = []
        for sc in scenes:
            new_id = sc.get("name") or sc["id"]
            scene_map[sc["id"]] = new_id
            aliases = [a for a in sc.get("aliases", []) if a and a != new_id]
            new_scenes.append(
                {
                    "id": new_id,
                    "aliases": aliases,
                    "perceivable": sc.get("perceivable", ""),
                    "region": sc.get("region", ""),
                }
            )
        sj.write_text(json.dumps(new_scenes, ensure_ascii=False, indent=2) + "\n", "utf-8")

    # --- npcs/*.json ---
    npc_map: dict[str, str] = {}
    ndir = wdir / "npcs"
    if ndir.is_dir():
        for f in sorted(ndir.glob("*.json")):
            card = json.loads(f.read_text("utf-8"))
            new_id = card.get("name") or card["id"]
            npc_map[card["id"]] = new_id
            card["id"] = new_id
            card.pop("name", None)
            card.pop("tags", None)
            for old in ndir.glob(f"{new_id}.json"):
                old.unlink()
            f.unlink()
            (ndir / f"{new_id}.json").write_text(
                json.dumps(card, ensure_ascii=False, indent=2) + "\n", "utf-8"
            )

    def remap(value: str) -> str:
        return scene_map.get(value) or npc_map.get(value) or value

    # --- saves ---
    sdir = wdir / "saves"
    if not sdir.is_dir():
        return
    for save in sorted(p for p in sdir.iterdir() if p.is_dir()):
        svj = save / "save.json"
        if svj.exists():
            sv = json.loads(svj.read_text("utf-8"))
            ps = sv.get("player_scene", "")
            if ps in scene_map:
                sv["player_scene"] = scene_map[ps]
            elif save.name == "main" and wdir.name in SPECIAL_PLAYER_SCENE:
                sv["player_scene"] = SPECIAL_PLAYER_SCENE[wdir.name]
            sv.pop("scene_name", None)
            svj.write_text(json.dumps(sv, ensure_ascii=False, indent=2) + "\n", "utf-8")

        evj = save / "events.jsonl"
        if evj.exists():
            lines = []
            for line in evj.read_text("utf-8").splitlines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("location"):
                    ev["location"] = remap(ev["location"])
                if isinstance(ev.get("participants"), list):
                    ev["participants"] = [remap(p) for p in ev["participants"]]
                if isinstance(ev.get("known_by"), list):
                    ev["known_by"] = [remap(k) for k in ev["known_by"]]
                if isinstance(ev.get("npc_moves"), list):
                    for mv in ev["npc_moves"]:
                        if mv.get("npc_id"):
                            mv["npc_id"] = remap(mv["npc_id"])
                        if mv.get("location"):
                            mv["location"] = remap(mv["location"])
                lines.append(json.dumps(ev, ensure_ascii=False))
            evj.write_text("\n".join(lines) + ("\n" if lines else ""), "utf-8")

    print(f"[{wdir.name}] scenes={scene_map or '{}'} npcs={npc_map or '{}'}")


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    for wdir in sorted(ROOT.iterdir()):
        if not wdir.is_dir():
            continue
        if only and wdir.name != only:
            continue
        migrate_world(wdir)


if __name__ == "__main__":
    main()
