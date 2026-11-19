import sqlite3,os,re,json
db=sqlite3.connect("file:/home/hermes/.hermes/state.db?mode=ro",uri=True)
CASES=[("01_day_of_meals",[12576,12609]),("02_two_drinks",[12784]),("03_lunch_snacks",[12710]),("04_restaurant_half",[12725]),
       ("05_day_plus_lunch",[11815]),("06_pill_coffee",[12680]),("07_workout_photos",[12735]),("08_yesterday_meal",[10862])]
os.makedirs("/tmp/hreplay/cases",exist_ok=True)
for name,ids in CASES:
    for k,rid in enumerate(ids,1):
        t=db.execute("select content from messages where rowid=?",(rid,)).fetchone()[0]
        t=re.sub(r"\[System note:.*?\]\s*$","",t,flags=re.S).strip()
        open(f"/tmp/hreplay/cases/{name}.t{k}.txt","w").write(t)
        imgs=re.findall(r"\[Image attached at: ([^\]]+)\]",t)
        print(name,k,len(t),"chars","images ok:",[os.path.exists(i) for i in imgs])
