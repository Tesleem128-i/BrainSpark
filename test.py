import google.generativeai as genai
genai.configure(api_key='AIzaSyADTWYIseuzXAYYEU2tpYNBrcVgWm5lJdQ')
for model in genai.list_models():
    print(model.name)