FROM node:20-alpine

WORKDIR /workspace/frontend

COPY package.json ./
RUN npm install

COPY . .

CMD ["npm", "run", "dev"]

