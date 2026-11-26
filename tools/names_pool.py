"""Replacement names for the scrambler, as (Latin, Devanagari) pairs.

Used only by tools/chaos_mutator.py to build test data. The pipeline never reads this file.
"""

GIVEN_MALE = [
    ("Aarav", "आरव"), ("Advait", "अद्वैत"), ("Akash", "आकाश"), ("Alok", "आलोक"), ("Amit", "अमित"),
    ("Anand", "आनंद"), ("Ankit", "अंकित"), ("Anil", "अनिल"), ("Arjun", "अर्जुन"), ("Ashwin", "अश्विन"),
    ("Bharat", "भरत"), ("Chetan", "चेतन"), ("Daksh", "दक्ष"), ("Devendra", "देवेंद्र"), ("Dinesh", "दिनेश"),
    ("Gaurav", "गौरव"), ("Girish", "गिरीश"), ("Harish", "हरीश"), ("Hemant", "हेमंत"), ("Imran", "इमरान"),
    ("Irfan", "इरफ़ान"), ("Jagdish", "जगदीश"), ("Jatin", "जतिन"), ("Kartik", "कार्तिक"), ("Kishore", "किशोर"),
    ("Lalit", "ललित"), ("Madhav", "माधव"), ("Manoj", "मनोज"), ("Mukul", "मुकुल"), ("Naveen", "नवीन"),
    ("Nikhil", "निखिल"), ("Nitin", "नितिन"), ("Omkar", "ओंकार"), ("Pankaj", "पंकज"), ("Pradeep", "प्रदीप"),
    ("Pranav", "प्रणव"), ("Rajat", "रजत"), ("Rakesh", "राकेश"), ("Rohit", "रोहित"), ("Sachin", "सचिन"),
    ("Sameer", "समीर"), ("Sanjay", "संजय"), ("Shailesh", "शैलेश"), ("Tarun", "तरुण"), ("Tushar", "तुषार"),
    ("Uday", "उदय"), ("Varun", "वरुण"), ("Vinod", "विनोद"), ("Yash", "यश"), ("Zubin", "ज़ुबिन"),
    ("Faizan", "फ़ैज़ान"), ("Tanveer", "तनवीर"), ("Shoaib", "शोएब"), ("Arshad", "अरशद"), ("Wasim", "वसीम"),
    ("Hitesh", "हितेश"), ("Keshav", "केशव"), ("Lokesh", "लोकेश"), ("Mayank", "मयंक"), ("Paras", "पारस"),
]

GIVEN_FEMALE = [
    ("Aarti", "आरती"), ("Alka", "अलका"), ("Ananya", "अनन्या"), ("Anjali", "अंजलि"), ("Bhavna", "भावना"),
    ("Chitra", "चित्रा"), ("Damini", "दामिनी"), ("Divya", "दिव्या"), ("Garima", "गरिमा"), ("Hema", "हेमा"),
    ("Ishita", "इशिता"), ("Jyotsna", "ज्योत्स्ना"), ("Kalpana", "कल्पना"), ("Komal", "कोमल"), ("Lata", "लता"),
    ("Madhuri", "माधुरी"), ("Manisha", "मनीषा"), ("Meenakshi", "मीनाक्षी"), ("Nandini", "नंदिनी"),
    ("Nirmala", "निर्मला"), ("Pallavi", "पल्लवी"), ("Pooja", "पूजा"), ("Preeti", "प्रीति"), ("Rachna", "रचना"),
    ("Rashmi", "रश्मि"), ("Rekha", "रेखा"), ("Ritu", "रितु"), ("Sapna", "सपना"), ("Seema", "सीमा"),
    ("Shalini", "शालिनी"), ("Shweta", "श्वेता"), ("Smita", "स्मिता"), ("Sonal", "सोनल"), ("Swati", "स्वाति"),
    ("Tanuja", "तनुजा"), ("Uma", "उमा"), ("Vandana", "वंदना"), ("Varsha", "वर्षा"), ("Yamini", "यामिनी"),
    ("Nazia", "नाज़िया"), ("Rukhsar", "रुख़सार"), ("Shabnam", "शबनम"), ("Farha", "फ़रहा"), ("Heena", "हिना"),
    ("Kavya", "काव्या"), ("Mohini", "मोहिनी"), ("Radhika", "राधिका"), ("Sarla", "सरला"), ("Trishna", "तृष्णा"),
]

SURNAMES = [
    ("Agarwal", "अग्रवाल"), ("Bhardwaj", "भारद्वाज"), ("Bisht", "बिष्ट"), ("Chandel", "चंदेल"), ("Dixit", "दीक्षित"),
    ("Dubey", "दुबे"), ("Gautam", "गौतम"), ("Joshi", "जोशी"), ("Kashyap", "कश्यप"), ("Khanna", "खन्ना"),
    ("Maurya", "मौर्य"), ("Nigam", "निगम"), ("Pathak", "पाठक"), ("Rajput", "राजपूत"), ("Rastogi", "रस्तोगी"),
    ("Saxena", "सक्सेना"), ("Shukla", "शुक्ला"), ("Tripathi", "त्रिपाठी"), ("Upadhyay", "उपाध्याय"),
    ("Vishwakarma", "विश्वकर्मा"), ("Bajpai", "बाजपेयी"), ("Rawat", "रावत"), ("Negi", "नेगी"), ("Qureshi", "क़ुरैशी"),
    ("Rizvi", "रिज़वी"), ("Naqvi", "नक़वी"), ("Farooqui", "फ़ारूक़ी"), ("Shaikh", "शेख़"), ("Mehra", "मेहरा"),
    ("Arora", "अरोड़ा"), ("Bansal", "बंसल"), ("Goyal", "गोयल"), ("Mittal", "मित्तल"), ("Jaiswal", "जायसवाल"),
    ("Sonkar", "सोनकर"), ("Kanaujia", "कनौजिया"), ("Rajbhar", "राजभर"), ("Prajapati", "प्रजापति"),
    ("Baghel", "बघेल"), ("Solanki", "सोलंकी"), ("Chaturvedi", "चतुर्वेदी"), ("Awasthi", "अवस्थी"),
    ("Kesarwani", "केसरवानी"), ("Bhatnagar", "भटनागर"), ("Sengar", "सेंगर"), ("Tomar", "तोमर"),
]

# Syllables, as (Latin, Devanagari) pairs, for invented names that exist in no name list.
SYLLABLES = [("vi", "वि"), ("ran", "रन"), ("tal", "ताल"), ("dus", "दुस"), ("mir", "मिर"), ("ke", "के"),
             ("sho", "शो"), ("lan", "लन"), ("bi", "बि"), ("thra", "थ्रा"), ("vul", "वुल"), ("roh", "रोह"),
             ("kre", "क्रे"), ("nim", "निम"), ("pal", "पल"), ("dro", "द्रो"), ("sku", "स्कु"), ("yel", "येल"),
             ("jor", "जोर"), ("vik", "विक"), ("har", "हर"), ("nu", "नु"), ("zo", "ज़ो"), ("gan", "गन")]
