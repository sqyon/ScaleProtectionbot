import json
import logging
import math
import os
import sys
from datetime import datetime, timedelta
from math import sqrt

import matplotlib.pyplot as plt
import telegram
from telegram.ext import Updater, CommandHandler

help_text = """欢迎使用本 bot，请使用如下命令：
/new_challenge 在本群开展减肥挑战 admin only
/end_challenge 结束本群的挑战 admin only
/delete_user 删除用户数据 admin only
/strategy 选择排名策略 admin only
/join_challenge 加入本群的减肥挑战
/weight 添加体重记录（只记录当天最后一条）
/height 修正身高记录（身高不统计变化，按常数计算）
/rank 查看指定天数的排名
/week 查看本周排名
/overall 查看总排名
/plot 查看指定天数的体重变化图
"""

challenges_path = './data/challenges.json'

metrics = {
	'1': {'name': '体重变化', 'expression': '原体重-现体重', 'key': lambda x: (x['weight'][0][1] - x['weight'][-1][1])},
	'2': {'name': '体重变化比例', 'expression': '(原体重-现体重)/原体重', 'key': lambda x: (x['weight'][0][1] - x['weight'][-1][1])},
	'3': {'name': '根号难度加权', 'expression': '(原体重-现体重)/√(初始体重-标准体重)，其中标准体重按照 BMI = 21 计算',
	      'key': lambda x: math.copysign(((x['weight'][0][1] - x['weight'][-1][1]) / (sqrt(abs(x['original_weight'] - 21 * x['height'] ** 2)))),
	                                     x['original_weight'] - 21 * x['height'] ** 2)},
}


def _get_timestamp():
	return str(datetime.now().timestamp())


def _get_timestr(timestamp):
	a = datetime.fromtimestamp(float(timestamp))
	return a.strftime('%Y-%m-%d %H:%M:%S')


def _is_today(timestamp):
	record = datetime.fromtimestamp(float(timestamp))
	today = datetime.now()
	return record.year == today.year and record.month == today.month and record.day == today.day


def _get_username(group_id, user_id):
	bot = telegram.Bot(token)
	return bot.get_chat_member(group_id, user_id).to_dict()['user']['username']


def _get_info(update):
	group_id = str(update.to_dict()['message']['chat']['id'])
	user_id = str(update.to_dict()['message']['from']['id'])
	username = update.to_dict()['message']['from']['username']
	message_id = update.to_dict()['message']['message_id']
	return group_id, user_id, username, message_id


def _get_challenges():
	if not os.path.exists(challenges_path):
		json.dump({}, open(challenges_path, "w"))
	return json.load(open(challenges_path, "r"))


def _get_challenge(group_path, update):
	group_id, user_id, username, message_id = _get_info(update)
	challenge_path = f'{group_path}/challenge.json'
	if not os.path.exists(challenge_path):
		tmp = {
			'group_id': group_id,
			'challenges': {}
		}
		json.dump(tmp, open(challenge_path, "w"))
	return json.load(open(challenge_path, "r"))


def _get_latest_challenge(update):
	group_id, user_id, username, message_id = _get_info(update)
	challenges = _get_challenges()
	challenge_cnt = str(challenges[group_id]['challenge_cnt'])
	group_path = f'./data/{group_id}'
	_ensure_path(group_path)
	challenge = _get_challenge(group_path, update)
	return challenge, challenge_cnt


def _get_scale(challenge_cnt_path):
	_ensure_path(challenge_cnt_path)
	scale_path = f'{challenge_cnt_path}/scale.json'
	if not os.path.exists(scale_path):
		json.dump({}, open(scale_path, "w"))
	return json.load(open(scale_path, "r"))


def _get_admin(group_id):
	bot = telegram.Bot(token)
	admins = bot.get_chat_administrators(group_id)
	ret = []
	for i in admins:
		ret.append(str(i.user['id']))
	return ret


def _is_admin(group_id, user_id):
	admins = _get_admin(group_id)
	return user_id in admins


def _admin_only(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	if not _is_admin(group_id, user_id):
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='admin only')
		return False
	return True


def _is_supergroup(update):
	return update.to_dict()['message']['chat']['type'] == 'supergroup'


def _in_challenge(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	challenge, challenge_cnt = _get_latest_challenge(update)

	if user_id not in challenge['challenges'][challenge_cnt]['challengers']:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'@{username} 你还未加入挑战哦')
		return False
	return True


def _ensure_scale(update):
	group_id, user_id, username, message_id = _get_info(update)
	challenge, challenge_cnt = _get_latest_challenge(update)
	scale_path = f'./data/{group_id}/{challenge_cnt}'
	scale = _get_scale(scale_path)
	if user_id not in scale:
		scale[user_id] = {'weight': []}
	return scale, scale_path


def _supergroup_only(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	if not _is_supergroup(update):
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='仅可在超级群组中使用本功能。')
		return False
	return True


def _ensure_path(path):
	if not os.path.exists(path):
		os.makedirs(path)


def _running_challenge_only(update, context):
	if not (_supergroup_only(update, context)):
		return False
	group_id, user_id, username, message_id = _get_info(update)
	challenges = _get_challenges()
	if group_id in challenges:
		if challenges[group_id]['status'] == 'ended':
			context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='没有正在进行的挑战')
			return False
	if group_id not in challenges:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'没有正在进行的挑战')
		return False
	return True


def _get_scale_data(update, context, time_limit, users=None):
	group_id, user_id, username, message_id = _get_info(update)
	scale, scale_path = _ensure_scale(update)
	if 'strategy' not in scale:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'请先使用 /strategy 指定比赛策略。')
		return
	strategy_id = scale['strategy']
	compare = metrics[strategy_id]['key']
	user_data = []
	for user_id, data in scale.items():
		if users and user_id not in users:
			continue
		if user_id == 'strategy' or user_id == 'deleted_user_data':
			continue
		username = _get_username(group_id, user_id)
		if 'height' not in data:
			context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'@{username} 没有添加过身高数据')
			continue
		if len(data['weight']) == 0:
			context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'@{username} 没有添加过体重数据')
			continue
		ret = {'username': username, 'height': data['height'], 'original_weight': data['weight'][0][1], 'weight': []}
		for data_timestamp, weight_data in data['weight'][::-1]:
			data_time = datetime.fromtimestamp(float(data_timestamp))
			if data_time >= time_limit:
				ret['weight'].append([data_timestamp, weight_data])
			else:
				if len(ret['weight']) == 0:
					context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'@{username} 在限定时间内没有添加体重数据')
					break
				last_time = datetime.fromtimestamp(float(ret['weight'][-1][0]))
				if abs(data_time - time_limit) > abs(last_time - time_limit):
					ret['weight'].append([data_timestamp, weight_data])
				break
		ret['weight'] = ret['weight'][::-1]
		ret['score'] = compare(ret)
		user_data.append(ret)
	return user_data


def _rank(update, context, time_limit):
	group_id, user_id, username, message_id = _get_info(update)
	user_data = _get_scale_data(update, context, time_limit)
	user_data.sort(key=lambda x: -x['score'])
	rank_list = '排名    username    体重变化    分数\n'
	for i, user in enumerate(user_data):
		rank_list += f'{i + 1} {user["username"]} {user["weight"][0][1] - user["weight"][-1][1]} {user["score"]}\n'
	context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=rank_list)


def print_help(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		print_help_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def print_help_(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=help_text)
	logging.info(context)
	logging.info(update)


def new_challenge(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		new_challenge_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def new_challenge_(update, context):
	if not (_supergroup_only(update, context) and _admin_only(update, context)):
		return
	group_id, user_id, username, message_id = _get_info(update)
	challenges = _get_challenges()
	if group_id in challenges:
		if challenges[group_id]['status'] != 'ended':
			context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='请先结束当前挑战')
			return
		challenges[group_id]['status'] = 'running'
		challenges[group_id]['challenge_cnt'] += 1
	if group_id not in challenges:
		challenges[group_id] = {}
		challenges[group_id]['status'] = 'running'
		challenges[group_id]['challenge_cnt'] = 1
	json.dump(challenges, open(challenges_path, "w"))
	challenge_cnt = challenges[group_id]['challenge_cnt']
	group_path = f'./data/{group_id}'
	_ensure_path(group_path)
	challenge = _get_challenge(group_path, update)
	challenge['challenges'][challenge_cnt] = {
		'start_time': _get_timestamp(),
		'start_user': user_id,
		'status': 'running',
		'end_time': None,
		'end_user': None,
		'challengers': [user_id]
	}
	json.dump(challenge, open(f'{group_path}/challenge.json', "w"))
	context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='挑战已开始，请各位参赛选手使用 /join_challenge 加入挑战')
	join_challenge(update, context)


def end_challenge(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		end_challenge_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def end_challenge_(update, context):
	if not (_running_challenge_only(update, context) and _admin_only(update, context)):
		return
	group_id, user_id, username, message_id = _get_info(update)
	challenges = _get_challenges()
	if group_id in challenges:
		challenges[group_id]['status'] = 'ended'
	json.dump(challenges, open(challenges_path, "w"))
	challenge_cnt = str(challenges[group_id]['challenge_cnt'])
	group_path = f'./data/{group_id}'
	_ensure_path(group_path)
	challenge = _get_challenge(group_path, update)
	challenge['challenges'][challenge_cnt]['end_time'] = _get_timestamp()
	challenge['challenges'][challenge_cnt]['end_user'] = user_id
	challenge['challenges'][challenge_cnt]['status'] = 'ended'
	json.dump(challenge, open(f'{group_path}/challenge.json', "w"))
	context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='挑战已结束!')


def join_challenge(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		join_challenge_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def join_challenge_(update, context):
	if not _running_challenge_only(update, context):
		return
	group_id, user_id, username, message_id = _get_info(update)
	challenge, challenge_cnt = _get_latest_challenge(update)
	if user_id in challenge['challenges'][challenge_cnt]['challengers']:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'@{username} 已经在挑战中了！')
		return
	challenge['challenges'][challenge_cnt]['challengers'].append(user_id)
	json.dump(challenge, open(f'./data/{group_id}/challenge.json', "w"))
	context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'@{username} 已加入挑战！')


def delete_user(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		delete_user_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def delete_user_(update, context):
	if not (_running_challenge_only(update, context) and _admin_only(update, context)):
		return
	group_id, user_id, username, message_id = _get_info(update)
	challenge, challenge_cnt = _get_latest_challenge(update)
	if user_id not in challenge['challenges'][challenge_cnt]['challengers']:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'@{username} 没有在挑战中！')
		return
	pos = challenge['challenges'][challenge_cnt]['challengers'].index(user_id)
	challenge['challenges'][challenge_cnt]['challengers'].pop(pos)
	json.dump(challenge, open(f'./data/{group_id}/challenge.json', "w"))

	scale, scale_path = _ensure_scale(update)
	if 'deleted_user_data' not in scale:
		scale['deleted_user_data'] = {}
	scale['deleted_user_data'][f'{user_id}_{datetime.now().strftime("%Y-%m-%d-%H:%M:%S")}'] = scale[user_id]
	json.dump(scale, open(f'{scale_path}/scale.json', "w"))
	context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'@{username} 已退出挑战！')


def weight(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		weight_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def weight_(update, context):
	if not (_running_challenge_only(update, context) and _in_challenge(update, context)):
		return
	group_id, user_id, username, message_id = _get_info(update)

	inputs = update.to_dict()['message']['text']
	try:
		inputs = inputs.split()[1]
		inputs = float(inputs)
		if inputs < 40 or inputs > 150:
			raise ValueError
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'请输入正确的体重数据')
		return

	scale, scale_path = _ensure_scale(update)

	new_data = (_get_timestamp(), inputs)
	if len(scale[user_id]['weight']) > 0 and _is_today(scale[user_id]['weight'][-1][0]):
		scale[user_id]['weight'].pop(-1)
	scale[user_id]['weight'].append(new_data)

	json.dump(scale, open(f'{scale_path}/scale.json', "w"))
	context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id,
	                         text=f'{_get_timestr(new_data[0])} @{username} 添加体重记录 {new_data[1]} 千克')


def height(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		height_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def height_(update, context):
	if not (_running_challenge_only(update, context) and _in_challenge(update, context)):
		return
	group_id, user_id, username, message_id = _get_info(update)

	inputs = update.to_dict()['message']['text']
	try:
		inputs = inputs.split()[1]
		inputs = float(inputs)
		if inputs < 1.50 or inputs > 2.20:
			raise ValueError
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'请输入正确的身高数据')
		return

	scale, scale_path = _ensure_scale(update)

	scale[user_id]['height'] = inputs

	json.dump(scale, open(f'{scale_path}/scale.json', "w"))
	context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id,
	                         text=f'@{username} 更新身高记录 {inputs} 米')


def strategy(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		strategy_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def strategy_(update, context):
	if not (_running_challenge_only(update, context) and _admin_only(update, context)):
		return
	group_id, user_id, username, message_id = _get_info(update)

	inputs = update.to_dict()['message']['text']
	try:
		inputs = inputs.split()[1]
		if inputs not in metrics:
			context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'请输入正确的比赛策略编号')
			return
	except:
		outputs = f'比赛策略如下，请输入需要的比赛策略编号：\n'
		for i, metirc in metrics.items():
			outputs += f'{i} : {metirc["name"]} {metirc["expression"]}\n'
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=outputs)
		return

	scale, scale_path = _ensure_scale(update)
	scale['strategy'] = inputs
	json.dump(scale, open(f'{scale_path}/scale.json', "w"))
	context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text=f'已成功切换为策略 {inputs}')


def week_rank(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		week_rank_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def week_rank_(update, context):
	if not _running_challenge_only(update, context):
		return
	today = datetime.now()
	today = datetime(today.year, today.month, today.day, 0, 0, 0, 0)
	time_limit = today - timedelta(days=7)
	_rank(update, context, time_limit)


def overall_rank(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		overall_rank_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def overall_rank_(update, context):
	if not _running_challenge_only(update, context):
		return
	_rank(update, context, datetime.min)


def rank(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		rank_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def rank_(update, context):
	if not _running_challenge_only(update, context):
		return
	group_id, user_id, username, message_id = _get_info(update)
	inputs = update.to_dict()['message']['text']
	try:
		inputs = inputs.split()[1]
		inputs = int(inputs)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='请输入整数。')
		return
	today = datetime.now()
	today = datetime(today.year, today.month, today.day, 0, 0, 0, 0)
	time_limit = today - timedelta(days=inputs)
	_rank(update, context, time_limit)


def plot(update, context):
	group_id, user_id, username, message_id = _get_info(update)
	try:
		plot_(update, context)
	except:
		context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='好像遇到了 bug，请联系 @sqyon')
		logging.exception("ERROR")
		return


def plot_(update, context):
	if not _running_challenge_only(update, context):
		return
	group_id, user_id, username, message_id = _get_info(update)
	inputs = update.to_dict()['message']['text']
	try:
		inputs = inputs.split()[1]
		inputs = int(inputs)
	except:
		# context.bot.send_message(chat_id=update.effective_chat.id, reply_to_message_id=message_id, text='请输入整数。')
		# return
		inputs = 10000
	today = datetime.now()
	today = datetime(today.year, today.month, today.day, 0, 0, 0, 0)
	time_limit = today - timedelta(days=inputs)
	user_data = _get_scale_data(update, context, time_limit, users=[user_id])[0]
	weights = []
	timestamps = []
	for i, j in user_data['weight']:
		timestamps.append(datetime.fromtimestamp((float(i))).strftime('%m-%d'))
		weights.append(j)
	plt.clf()
	plt.plot(timestamps, weights, label=f'@{username}')
	plt.title(f'@{username} in last {inputs} days')
	plt.xlabel('time')
	plt.ylabel('weight')
	_ensure_path(f'./pic')
	plt.savefig(f'./pic/{username}.png', dpi=120)
	context.bot.send_photo(chat_id=update.effective_chat.id, reply_to_message_id=message_id, photo=open(f'./pic/{username}.png', 'rb'))


def main(token):
	updater = Updater(token=token, use_context=True)
	dp = updater.dispatcher
	logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO, filename="bot.log", filemode="a")
	# logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

	if not os.path.exists('./data'):
		os.makedirs('./data')

	dp.add_handler(CommandHandler('help', print_help))

	dp.add_handler(CommandHandler('new_challenge', new_challenge))
	dp.add_handler(CommandHandler('end_challenge', end_challenge))
	dp.add_handler(CommandHandler('join_challenge', join_challenge))
	dp.add_handler(CommandHandler('delete_user', delete_user))

	dp.add_handler(CommandHandler('weight', weight))
	dp.add_handler(CommandHandler('height', height))

	dp.add_handler(CommandHandler('strategy', strategy))
	dp.add_handler(CommandHandler('rank', rank))
	dp.add_handler(CommandHandler('week', week_rank))
	dp.add_handler(CommandHandler('overall', overall_rank))

	dp.add_handler(CommandHandler('plot', plot))

	updater.start_polling()
	updater.idle()


if __name__ == '__main__':
	token = sys.argv[1]
	main(token=token)
