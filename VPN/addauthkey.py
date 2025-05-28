import os

# directory containing the .ovpn 
vpn_dir = r"C:\Games\ArmagetronProject2.0\arma_chatbot_config\VPN\OVPN"

# path to the credentials 
credentials_file = r"C:\\Users\\itsne\\Desktop\\arma_chatbot_config\\VPN\\mycredentials.txt"


for filename in os.listdir(vpn_dir):
    if filename.endswith(".ovpn"):
        filepath = os.path.join(vpn_dir, filename)
        
        with open(filepath, 'r') as f:
            file_contents = f.readlines()
        
        found = False
        for i, line in enumerate(file_contents):
            if "auth-user-pass" in line:
                file_contents[i] = f"auth-user-pass {credentials_file}\n"
                found = True
                break

        if not found:
            file_contents.append(f"auth-user-pass {credentials_file}\n")
        
        with open(filepath, 'w') as f:
            f.writelines(file_contents)

print("All files updated successfully.")
