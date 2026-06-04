
gh secret set TEST_ACCOUNT_EMAIL --body athithya851@gmail.com
gh secret set TEST_ACCOUNT_PASSWORD --body $TEST_ACCOUNT_PASSWORD
aws ssm put-parameter --name "/agentops/admin-allowed-google-domains" --type "String" --value "gmail.com" --overwrite --region ap-south-1
bash src/offline/commands.sh


