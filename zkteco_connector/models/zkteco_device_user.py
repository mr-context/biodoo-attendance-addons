# -*- coding: utf-8 -*-
import logging
import random as _rnd
from difflib import SequenceMatcher
from odoo import models, fields, api

_logger = logging.getLogger(__name__)


class ZktecoDeviceUser(models.Model):
    _name = 'zkteco.device.user'
    _description = 'ZKTeco Device User — Sas'
    _order = 'device_id, state, pin'
    _rec_name = 'name_on_device'

    device_id      = fields.Many2one('zkteco.device', required=True, ondelete='cascade', index=True)
    pin            = fields.Char(required=True, string='PIN device', readonly=True)
    name_on_device = fields.Char(string='Nom sur le device', readonly=True)
    privilege      = fields.Integer(string='Privilège', readonly=True)
    card           = fields.Char(string='Carte', readonly=True)

    state = fields.Selection([
        ('new',     'Nouveau'),
        ('mapped',  'Mappé'),
        ('ignored', 'Ignoré'),
        ('deleted', 'Supprimé du device'),
    ], default='new', string='État', index=True)

    employee_id = fields.Many2one('hr.employee', string='Employé Odoo', index=True)

    biodata_ids  = fields.One2many('zkteco.device.biodata',  'device_user_id', string='Templates biométriques')
    biophoto_ids = fields.One2many('zkteco.device.biophoto', 'device_user_id', string="Photos d'enrôlement")

    pending_attlog_count = fields.Integer(compute='_compute_counts', string='Pointages en attente')
    biodata_count        = fields.Integer(compute='_compute_counts', string='Templates')
    biophoto_count       = fields.Integer(compute='_compute_counts', string='Photos')
    finger_grid_html     = fields.Html(compute='_compute_finger_grid', string='Empreintes', sanitize=False)
    face_preview_html    = fields.Html(compute='_compute_face_preview',  string='Visage',     sanitize=False)
    palm_preview_html    = fields.Html(compute='_compute_palm_preview',  string='Paume',      sanitize=False)
    has_palm             = fields.Boolean(compute='_compute_palm_preview', string='Paume enrôlée')

    _unique_device_pin = models.Constraint(
        'UNIQUE(device_id, pin)', 'PIN unique par device')

    # (finger_id, cx, cy, label, label_above)
    # Positions calibrées avec l'outil HTML drag-and-drop sur la main potrace.
    # Droite : abs x=[449..688], y=[20..258]  |  Gauche (miroir) : abs x=[58..277]
    # ZKTeco convention officielle (ADMS spec v5.4) :
    #   FID 0-4 = main GAUCHE : 0=auriculaire, 1=annulaire, 2=majeur, 3=index, 4=pouce
    #   FID 5-9 = main DROITE : 5=pouce, 6=index, 7=majeur, 8=annulaire, 9=auriculaire
    # Vue anatomique (face à l'utilisateur) : main droite → côté gauche du SVG,
    # main gauche → côté droit du SVG.
    _FINGER_POS = [
        # Left hand (FID 0-4) — RIGHT side of SVG (x=449-688)
        # 0=little→leftmost, 4=thumb→rightmost (palm facing you, left hand)
        (0, 449,  42, 'Aur.G', False),  # auriculaire (petit doigt)
        (1, 531,  20, 'Ann.G', False),  # annulaire
        (2, 596,  42, 'Maj.G', False),  # majeur
        (3, 662,  74, 'Ind.G', False),  # index
        (4, 688, 258, 'Pou.G', True),   # pouce
        # Right hand (FID 5-9) — LEFT side of SVG (x=58-277)
        # 5=thumb→leftmost, 9=little→rightmost (palm facing you, right hand)
        (5,  58, 260, 'Pou.D', True),   # pouce
        (6,  73,  81, 'Ind.D', False),  # index
        (7, 133,  40, 'Maj.D', False),  # majeur
        (8, 209,  23, 'Ann.D', False),  # annulaire
        (9, 277,  53, 'Aur.D', False),  # auriculaire (petit doigt)
    ]

    # Potrace paths from static/img/hand.svg — right hand, palm view, fingers up.
    # Coordinate space: potrace (x in [770..9147], y in [3040..12759]).
    # Rendered via group transform translate(0,1280) scale(0.1,-0.1) in the source SVG.
    _HAND_PATHS = (
        'M3585 12759 c-95 -48 -122 -72 -184 -165 -34 -51 -58 -104 -84 -185 -42 -134 -67 -244 -67 -294 0 -20 -5 -45 -11 -56 -29 -55 -62 -354 -79 -709 l-10 -215 32 -27 c18 -16 42 -28 53 -28 12 0 25 -4 31 -10 5 -5 49 -13 97 -16 80 -6 87 -9 87 -29 0 -31 -17 -50 -90 -97 -45 -30 -71 -55 -85 -83 -15 -31 -33 -46 -77 -68 -32 -16 -82 -51 -113 -78 -48 -45 -58 -60 -80 -132 -33 -105 -42 -177 -55 -437 -5 -118 -14 -287 -20 -375 -27 -453 -32 -589 -25 -652 13 -126 52 -183 126 -183 35 0 39 -3 39 -25 0 -18 -22 -43 -94 -104 -105 -91 -119 -114 -161 -277 -39 -152 -44 -381 -15 -619 23 -189 33 -219 96 -294 86 -102 301 -152 436 -101 117 44 244 184 338 375 66 133 108 266 116 365 3 36 12 79 20 97 18 39 19 278 2 335 -14 47 -54 84 -111 99 -55 15 -209 6 -351 -21 -143 -26 -216 -27 -216 -2 0 24 54 74 107 101 86 43 387 111 492 111 35 0 65 -6 77 -15 22 -16 90 -20 99 -5 10 15 -14 49 -48 70 -28 17 -51 20 -178 20 -80 0 -194 -7 -253 -15 -175 -25 -203 0 -84 74 99 62 122 66 285 55 170 -13 217 -5 244 39 10 18 19 42 19 55 0 13 27 92 59 175 59 152 144 453 157 557 4 30 10 60 14 65 4 6 13 80 20 165 10 132 10 165 -3 226 -25 119 -56 171 -147 248 -31 27 -44 31 -140 39 -91 9 -132 6 -315 -17 -115 -14 -225 -26 -242 -26 -30 0 -33 3 -33 29 0 25 7 31 50 50 28 12 76 27 108 33 54 10 87 9 370 -8 92 -6 102 -5 102 10 0 27 -78 55 -202 72 -165 23 -160 22 -156 46 5 34 73 50 193 46 91 -3 107 -6 180 -40 134 -62 131 -63 195 34 155 238 273 761 257 1148 -6 159 -37 362 -64 417 -21 45 -179 216 -207 224 -12 3 -49 20 -83 38 -61 30 -65 31 -200 31 l-138 0 -80 -41z',
        'M6510 11950 c-187 -51 -290 -116 -388 -246 -96 -126 -174 -257 -228 -377 -25 -56 -56 -122 -70 -147 -14 -25 -37 -83 -50 -130 -35 -117 -234 -720 -300 -904 -32 -90 -57 -177 -61 -216 -6 -62 -9 -68 -65 -129 -77 -84 -161 -256 -224 -456 -26 -82 -94 -283 -151 -445 -107 -303 -124 -370 -104 -409 17 -31 45 -39 191 -55 141 -16 170 -26 170 -59 0 -16 -11 -26 -40 -39 -65 -27 -54 -38 53 -53 41 -6 47 -10 47 -31 0 -30 -13 -33 -155 -36 -133 -4 -160 -15 -256 -105 -119 -113 -158 -205 -195 -463 -19 -130 -13 -298 15 -430 26 -123 91 -255 141 -288 19 -12 43 -29 53 -37 29 -25 150 -19 220 9 103 43 225 126 303 209 94 99 159 198 239 362 99 200 137 333 111 383 -18 34 -129 141 -179 171 -37 22 -57 48 -57 72 0 8 15 10 49 7 38 -4 64 -15 114 -51 73 -52 91 -56 145 -28 122 62 354 439 417 676 15 55 56 205 91 334 35 128 64 244 64 256 0 25 -39 59 -109 95 -86 43 -387 151 -505 180 -130 32 -166 54 -166 101 0 47 22 59 111 59 73 0 82 2 102 25 31 36 106 47 249 39 80 -4 121 -11 129 -20 14 -17 287 -104 326 -104 41 0 93 55 93 98 0 81 71 356 111 431 10 19 19 41 19 48 0 12 71 195 168 435 129 316 242 702 242 825 -1 198 -160 395 -365 449 -70 19 -226 16 -305 -6z',
        'M770 11801 c-116 -38 -200 -128 -256 -277 -44 -118 -63 -228 -75 -441 -13 -221 -5 -316 29 -363 51 -69 148 -96 303 -85 105 8 139 -1 139 -35 0 -11 -10 -26 -22 -34 -38 -24 -184 -49 -247 -42 -109 13 -106 13 -109 -16 -4 -35 15 -103 34 -123 24 -24 61 -18 117 20 59 40 93 45 102 15 9 -27 -8 -46 -67 -76 -92 -46 -128 -144 -148 -404 -22 -268 -2 -476 63 -653 36 -100 145 -283 202 -342 88 -90 157 -102 314 -55 77 24 137 21 148 -6 12 -33 -67 -86 -147 -99 -60 -10 -112 -37 -145 -77 -27 -31 -27 -36 -21 -113 6 -84 30 -248 72 -500 29 -176 104 -451 149 -551 53 -115 148 -174 283 -174 55 0 76 5 117 29 185 106 238 231 250 591 9 269 -4 404 -66 670 -27 118 -53 217 -58 220 -4 3 -12 24 -18 48 -6 23 -30 74 -53 112 -42 69 -42 71 -36 138 17 174 17 222 2 337 -36 261 -88 447 -217 765 -92 228 -92 231 -109 661 -10 284 -26 484 -41 544 -30 115 -138 243 -246 292 -44 21 -77 27 -138 30 -44 1 -91 -1 -105 -6z',
        'M8930 10540 c0 -5 -19 -10 -42 -10 -119 0 -329 -101 -438 -210 -73 -73 -308 -405 -462 -653 -105 -169 -258 -487 -258 -536 0 -10 -12 -33 -26 -52 -46 -62 -94 -138 -133 -217 -64 -127 -69 -115 66 -146 65 -15 151 -43 191 -61 76 -36 152 -91 152 -112 0 -23 -68 -14 -176 22 -57 19 -109 35 -114 35 -23 0 -7 -35 39 -83 52 -56 71 -86 71 -113 0 -14 -8 -16 -55 -12 -43 4 -87 20 -190 71 -74 37 -143 67 -153 67 -34 0 -87 -66 -359 -455 -219 -313 -314 -463 -358 -568 l-15 -37 65 -32 c55 -27 65 -36 65 -58 0 -20 -7 -29 -33 -38 -42 -15 -66 -15 -144 1 -34 8 -68 10 -74 6 -6 -4 -14 -30 -17 -58 -4 -49 -6 -52 -48 -70 -54 -24 -138 -111 -213 -221 -110 -161 -283 -461 -308 -532 -32 -93 -30 -208 5 -280 57 -116 163 -190 283 -196 182 -11 310 46 500 221 31 28 76 63 99 77 56 33 213 186 235 229 39 76 26 206 -26 266 -30 36 -72 138 -77 189 -5 46 -11 58 -76 128 -38 42 -74 86 -78 97 -11 31 15 45 76 38 64 -6 95 -21 161 -77 28 -23 75 -54 105 -70 30 -16 90 -60 133 -99 l78 -71 36 15 c42 18 157 108 228 180 86 87 277 382 365 566 61 128 61 140 2 234 -48 76 -91 180 -79 191 20 20 81 -32 132 -112 19 -29 38 -55 44 -58 16 -10 51 12 51 32 0 34 -49 118 -102 177 -57 63 -88 104 -88 117 0 5 12 8 26 8 30 0 100 -39 170 -96 27 -23 63 -44 80 -47 16 -3 47 -10 69 -16 39 -10 41 -9 72 27 47 54 83 117 83 146 0 14 4 27 9 31 6 3 24 49 41 103 18 53 52 135 76 182 49 95 111 228 173 370 58 133 103 190 166 210 92 28 173 131 324 411 55 102 127 317 142 419 10 73 9 98 -5 165 -24 115 -55 172 -138 250 -76 72 -133 104 -185 105 -18 0 -33 5 -33 10 0 6 -25 10 -55 10 -30 0 -55 -4 -55 -10z',
        'M2225 7029 c-16 -6 -97 -14 -180 -19 -180 -10 -353 -42 -414 -76 -24 -13 -51 -24 -61 -24 -10 0 -23 -7 -30 -16 -26 -30 -187 -149 -274 -202 -329 -198 -470 -354 -654 -727 -120 -243 -162 -349 -162 -411 0 -24 -17 -74 -44 -133 -84 -181 -110 -306 -155 -751 -23 -222 -45 -396 -51 -398 -6 -2 -10 -37 -10 -80 0 -96 -30 -365 -75 -670 -19 -129 -42 -293 -51 -365 -8 -73 -25 -175 -36 -227 -27 -130 -32 -258 -19 -430 19 -240 41 -390 66 -445 13 -27 37 -84 54 -126 62 -150 178 -316 299 -430 187 -174 402 -299 652 -378 178 -56 424 -111 494 -111 29 0 100 32 210 95 44 25 62 29 185 36 75 5 188 13 251 18 129 12 234 14 268 5 16 -5 22 -14 22 -35 0 -34 -31 -66 -74 -74 -42 -9 -113 -51 -126 -75 -10 -18 0 -25 102 -76 62 -31 131 -69 153 -84 78 -53 280 -160 302 -160 32 0 30 23 -13 193 -32 127 -35 151 -30 229 11 168 73 434 121 528 13 25 37 77 54 115 45 107 195 375 236 422 27 32 44 43 66 43 26 0 29 -3 29 -33 0 -35 -16 -61 -117 -198 -73 -100 -133 -210 -133 -244 0 -13 -11 -40 -24 -60 -28 -41 -59 -168 -77 -313 -10 -82 -10 -129 0 -230 49 -507 216 -763 639 -976 82 -41 182 -73 276 -87 39 -6 76 -15 81 -19 6 -4 92 -14 191 -21 206 -14 297 -6 438 37 232 73 414 211 777 592 160 167 198 201 265 239 161 91 312 220 428 365 73 91 206 335 206 378 0 15 7 34 16 41 15 12 16 32 11 159 -6 167 -15 200 -117 424 -60 132 -62 139 -55 194 16 133 107 324 206 433 45 50 81 103 101 146 27 61 30 75 25 136 -3 38 -10 74 -15 80 -5 7 -13 28 -17 47 -26 121 -182 300 -261 300 -18 0 -34 8 -42 20 -37 60 -158 102 -483 169 -248 51 -367 82 -405 106 -12 8 -83 19 -165 26 -140 13 -145 14 -175 43 -23 23 -35 50 -48 103 -19 76 -26 86 -65 96 -30 7 -200 -11 -224 -24 -10 -5 -55 -14 -100 -19 -141 -17 -257 -46 -257 -63 0 -19 55 -72 106 -102 46 -27 402 -187 445 -200 13 -4 40 -22 59 -40 19 -18 53 -41 75 -51 56 -25 249 -222 318 -324 61 -91 76 -135 58 -169 -16 -30 -41 -15 -99 62 -26 34 -96 114 -155 177 -60 63 -111 118 -115 123 -14 15 -179 134 -232 167 -78 47 -445 233 -566 286 -56 24 -117 44 -137 44 -19 0 -67 -14 -107 -30 -85 -35 -113 -37 -200 -10 -125 38 -311 20 -534 -52 -65 -21 -218 -91 -424 -196 -277 -139 -338 -174 -418 -239 -52 -42 -98 -73 -101 -69 -27 27 121 177 279 282 140 93 420 235 559 283 193 67 445 181 463 210 12 18 -161 51 -271 51 -184 1 -428 25 -538 55 -348 92 -574 206 -631 317 -8 15 -10 28 -6 28 5 0 45 -23 88 -51 142 -93 369 -188 572 -240 95 -24 136 -29 233 -29 75 0 120 4 125 11 10 16 -28 62 -68 81 -19 9 -79 31 -134 48 -133 42 -135 43 -94 48 66 7 385 -54 480 -92 19 -8 59 -17 88 -20 29 -3 68 -10 85 -15 18 -6 77 -22 132 -36 55 -15 145 -45 200 -68 l100 -41 335 0 c412 1 622 19 778 67 61 18 102 36 102 44 0 7 -17 31 -37 54 -46 51 -179 116 -398 194 -88 31 -169 63 -180 69 -11 7 -38 16 -60 20 -22 4 -94 23 -160 42 -146 43 -472 124 -501 124 -11 0 -29 7 -40 15 -10 8 -35 15 -55 15 -20 0 -126 18 -235 40 -110 22 -214 40 -233 40 -18 0 -95 11 -170 24 -75 14 -193 27 -263 31 -131 6 -231 25 -258 50 -8 7 -28 16 -45 20 -65 13 -124 29 -221 56 -239 68 -474 92 -737 76 l-178 -11 3 -26 c3 -21 20 -33 108 -77 58 -29 114 -53 125 -53 11 0 65 -16 120 -35 55 -19 118 -35 139 -35 22 0 47 -7 57 -16 13 -11 49 -20 110 -25 63 -6 98 -14 111 -26 10 -9 15 -19 11 -23 -5 -3 -80 -3 -168 0 -132 5 -189 13 -321 42 -89 20 -179 45 -200 56 -22 11 -79 34 -129 52 -236 86 -309 161 -281 290 6 30 13 37 47 48 69 21 163 15 268 -19 76 -25 120 -32 214 -36 64 -3 117 -9 117 -13 0 -4 44 -10 98 -14 212 -15 393 -38 514 -67 71 -17 130 -35 134 -40 3 -5 14 -9 24 -9 10 0 54 -16 97 -36 61 -28 98 -38 169 -44 51 -5 121 -14 155 -19 35 -5 125 -13 199 -17 74 -3 140 -10 145 -14 6 -4 53 -10 105 -14 393 -28 983 -163 1203 -276 44 -22 85 -40 93 -40 23 0 189 -64 397 -152 176 -75 194 -86 297 -169 132 -106 163 -118 261 -101 38 7 82 12 97 12 56 0 321 107 407 165 154 102 289 277 341 440 34 106 46 330 21 397 -8 24 -18 61 -21 82 -3 22 -20 63 -39 90 -18 28 -54 83 -80 121 -53 81 -215 261 -302 336 -93 82 -213 168 -280 201 -73 37 -160 68 -191 68 -33 0 -40 -39 -20 -111 9 -32 16 -79 16 -104 0 -44 -1 -45 -32 -45 -28 1 -37 8 -61 45 -50 79 -320 317 -490 431 -37 25 -111 67 -166 94 -79 39 -100 54 -111 79 -16 39 -64 87 -110 110 -91 46 -212 57 -410 37 -85 -9 -174 -16 -198 -16 -24 0 -65 -7 -91 -16 -71 -23 -490 -17 -517 7 -11 9 -35 24 -54 34 -19 9 -55 34 -80 54 -68 57 -213 129 -314 157 -50 14 -96 30 -103 36 -7 5 -36 16 -65 24 -62 16 -384 18 -433 3z',
        'M4935 4159 c-4 -6 -4 -30 -1 -53 6 -36 14 -48 57 -80 77 -57 104 -65 250 -71 123 -5 141 -9 248 -47 l116 -42 208 -4 207 -4 33 34 c44 45 65 108 49 152 -7 18 -12 39 -12 47 0 18 -45 39 -86 39 -17 0 -51 -7 -74 -15 -72 -25 -167 -19 -262 16 -71 26 -96 31 -171 30 -74 -1 -93 -5 -118 -23 -24 -18 -43 -22 -117 -22 -75 -1 -94 3 -143 27 -42 20 -71 27 -117 27 -33 0 -64 -5 -67 -11z',
        'M9147 3040 c-107 -25 -186 -49 -215 -66 -116 -66 -229 -147 -251 -180 -36 -52 -174 -154 -210 -154 -5 0 -13 10 -17 23 -17 57 -72 74 -123 38 -15 -11 -47 -47 -72 -81 -46 -64 -123 -121 -192 -142 -43 -13 -56 -5 -39 22 29 47 4 98 -42 86 -39 -10 -80 -50 -144 -144 -33 -49 -67 -94 -76 -101 -12 -10 -56 -6 -231 20 l-216 32 -237 -27 c-130 -15 -249 -32 -263 -37 -32 -13 -155 -135 -205 -204 -45 -62 -83 -146 -94 -205 -5 -25 -15 -63 -24 -85 -31 -81 -12 -293 35 -384 33 -63 99 -93 189 -84 38 3 76 14 92 24 39 28 147 75 240 104 72 23 244 56 633 121 106 18 205 53 242 87 22 19 74 103 124 197 27 51 56 64 61 28 2 -13 -7 -102 -19 -198 -37 -294 -37 -294 85 -261 97 26 155 26 604 4 472 -24 580 -23 693 6 50 13 122 29 160 37 213 40 399 121 564 245 162 122 261 307 261 489 -1 170 -68 351 -172 465 -48 53 -223 177 -314 223 -77 39 -375 123 -469 133 -140 13 -178 10 -358 -31z',
    )

    # Mesh grid — coords locaux depuis l'ancre (dx, dy) de chaque main.
    # Couvre local_x=[30..283], local_y=[-60..192] pour englober toute la silhouette.
    _MESH_COL = [30 + i * 23 for i in range(12)]   # [30, 53, ..., 283]
    _MESH_ROW = [-60 + i * 21 for i in range(13)]  # [-60, -39, ..., 192]

    # SVG transforms calibrés avec l'outil HTML (sx=sy=0.250, ox=111, oy=1000).
    _TR_RIGHT = 'translate(411,75) scale(0.250,0.250) translate(111,1000) scale(0.1,-0.1)'
    _TR_LEFT  = 'translate(330,75) scale(-0.250,0.250) translate(111,1000) scale(0.1,-0.1)'

    @api.depends('biodata_ids.bio_type', 'biodata_ids.valid', 'biodata_ids.finger_id')
    def _compute_finger_grid(self):
        CR = 20
        W, H = 740, 365
        DY = 65

        def _fp(cx, cy, ok):
            c = '#fff' if ok else '#94a3b8'
            s = 1.4
            ox = round(cx - 12 * s, 1)
            oy = round(cy - 12 * s, 1)
            return (
                f'<g transform="translate({ox},{oy}) scale({s})" fill="none"'
                f' stroke="{c}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">'
                '<path d="M8.10008 21.221C6.71021 19.2375 5.89258 16.8243 5.89258 14.2187'
                'C5.89258 10.8443 8.6265 8.10938 11.9989 8.10938C15.3712 8.10938 18.1051 10.8443 18.1051 14.2187"/>'
                '<path d="M18.4359 20.3118C18.3259 20.3179 18.218 20.3281 18.107 20.3281'
                'C14.7347 20.3281 12.0007 17.5931 12.0007 14.2188"/>'
                '<path d="M13.2694 21.9999C10.675 20.382 8.94705 17.5024 8.94705 14.2187'
                'C8.94705 12.5315 10.3145 11.164 12.0007 11.164C13.6869 11.164 15.0543 12.5315 15.0543 14.2187'
                'C15.0543 15.9059 16.4218 17.2733 18.108 17.2733C19.7942 17.2733 21.1616 15.9059 21.1616 14.2187'
                'C21.1616 9.1571 17.0602 5.05469 12.0017 5.05469C6.94319 5.05469 2.8418 9.1571 2.8418 14.2187'
                'C2.8418 15.3469 2.96806 16.4455 3.20021 17.5045"/>'
                '<path d="M20.5257 5.86313C18.4435 3.4978 15.399 2 12.0002 2'
                'C8.60136 2 5.55687 3.4978 3.47461 5.86313"/>'
                '</g>'
            )

        for r in self:
            enrolled = {b.finger_id for b in r.biodata_ids if b.bio_type == '1' and b.valid}

            circles = labels = ''
            for fid, cx, cy, lbl, above in self._FINGER_POS:
                cyd    = cy + DY
                ok     = fid in enrolled
                c_ring = '#15803d' if ok else '#475569'
                c_fill = '#22c55e' if ok else 'rgba(255,255,255,0.2)'
                c_lbl  = '#166534' if ok else '#1e293b'

                circles += (
                    f'<circle cx="{cx}" cy="{cyd}" r="{CR+3}" fill="{c_ring}" opacity="0.9"/>'
                    f'<circle cx="{cx}" cy="{cyd}" r="{CR}" fill="{c_fill}"/>'
                    + _fp(cx, cyd, ok)
                )
                ly = (cyd - CR - 9) if above else (cyd + CR + 14)
                labels += (
                    f'<rect x="{cx-19}" y="{ly-10}" width="38" height="13" rx="3"'
                    f' fill="white" fill-opacity="0.88"/>'
                    f'<text x="{cx}" y="{ly}" text-anchor="middle"'
                    f' fill="{c_lbl}" font-size="9" font-weight="700">{lbl}</text>'
                )

            r.finger_grid_html = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}"'
                f' style="font-family:ui-sans-serif,system-ui,sans-serif;display:block;width:100%;max-width:{W}px;height:auto">'
                f'<circle cx="90" cy="18" r="6" fill="#94a3b8"/>'
                f'<text x="102" y="22" fill="#64748b" font-size="10">Non enrôlé</text>'
                f'<circle cx="215" cy="18" r="6" fill="#22c55e"/>'
                f'<text x="227" y="22" fill="#64748b" font-size="10">Enrôlé</text>'
                f'<line x1="370" y1="30" x2="370" y2="{H-8}" stroke="#e2e8f0" stroke-width="1"/>'
                f'<text x="185" y="46" text-anchor="middle" fill="#3b82f6"'
                f' font-size="11" font-weight="700" letter-spacing="1">MAIN DROITE</text>'
                f'<text x="555" y="46" text-anchor="middle" fill="#3b82f6"'
                f' font-size="11" font-weight="700" letter-spacing="1">MAIN GAUCHE</text>'
                + circles + labels
                + '</svg>'
            )

    @api.depends('biodata_ids.bio_type', 'biophoto_ids.photo', 'biophoto_ids.captured_at')
    def _compute_face_preview(self):
        # Même hauteur (FH) que finger_grid_html pour un alignement homogène.
        FONT = 'font-family="ui-sans-serif,system-ui,sans-serif"'
        FW, FH = 280, 365          # largeur / hauteur du SVG
        PX, PY = 10, 44            # coin supérieur-gauche de la zone photo
        PW = FW - PX * 2           # 130
        PH = FH - PY - 8           # 313
        MX = FW // 2               # centre horizontal  75

        for r in self:
            has_face = any(b.bio_type in ('9', '2') for b in r.biodata_ids)
            fc = '#22c55e' if has_face else '#94a3b8'
            ft = '✓ Visage' if has_face else '— Visage'

            photo = r.biophoto_ids.sorted('captured_at', reverse=True)[:1]
            if photo and photo.photo:
                img_el = (
                    f'<defs><clipPath id="fpc{r.id}">'
                    f'<rect x="{PX}" y="{PY}" width="{PW}" height="{PH}" rx="8"/>'
                    f'</clipPath></defs>'
                    f'<image href="/web/image/zkteco.device.biophoto/{photo.id}/photo"'
                    f' x="{PX}" y="{PY}" width="{PW}" height="{PH}"'
                    f' preserveAspectRatio="xMidYMid slice"'
                    f' clip-path="url(#fpc{r.id})"/>'
                )
            else:
                ty = PY + PH // 2
                img_el = (
                    f'<text x="{MX}" y="{ty - 6}" text-anchor="middle" {FONT}'
                    f' font-size="11" fill="#cbd5e1">Pas de</text>'
                    f'<text x="{MX}" y="{ty + 10}" text-anchor="middle" {FONT}'
                    f' font-size="11" fill="#cbd5e1">photo</text>'
                )

            r.face_preview_html = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {FW} {FH}"'
                f' style="display:block;width:{FW}px;height:{FH}px">'
                # badge
                f'<rect x="5" y="6" width="{FW-10}" height="28" rx="14" fill="{fc}"/>'
                f'<text x="{MX}" y="24" text-anchor="middle" {FONT}'
                f' font-size="12" font-weight="600" fill="white">{ft}</text>'
                # cadre photo
                f'<rect x="{PX}" y="{PY}" width="{PW}" height="{PH}" rx="8"'
                f' fill="#f8fafc" stroke="#e2e8f0" stroke-width="1.5"/>'
                + img_el
                + '</svg>'
            )

    @api.depends('biodata_ids.bio_type', 'biodata_ids.valid', 'biodata_ids.finger_id')
    def _compute_palm_preview(self):
        FONT = 'font-family="ui-sans-serif,system-ui,sans-serif"'
        FW, FH = 150, 365
        MX = FW // 2

        # Icône main (Lucide "hand") centrée, mise à l'échelle.
        HAND = (
            '<path d="M18 11V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2"/>'
            '<path d="M14 10V4a2 2 0 0 0-2-2a2 2 0 0 0-2 2v2"/>'
            '<path d="M10 10.5V6a2 2 0 0 0-2-2a2 2 0 0 0-2 2v8"/>'
            '<path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34'
            'l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15"/>'
        )

        for r in self:
            palms = r.biodata_ids.filtered(lambda b: b.bio_type == '8')
            ok = bool(palms.filtered('valid')) or bool(palms)
            r.has_palm = ok
            c  = '#22c55e' if ok else '#94a3b8'
            t  = '✓ Paume' if ok else '— Paume'
            n  = len(palms)
            sub = (f'{n} échantillon{"s" if n > 1 else ""}') if ok else 'Non enrôlée'

            iy = FH // 2 - 30
            r.palm_preview_html = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {FW} {FH}"'
                f' style="display:block;width:{FW}px;height:{FH}px">'
                f'<rect x="5" y="6" width="{FW-10}" height="28" rx="14" fill="{c}"/>'
                f'<text x="{MX}" y="24" text-anchor="middle" {FONT}'
                f' font-size="12" font-weight="600" fill="white">{t}</text>'
                f'<rect x="10" y="44" width="{FW-20}" height="{FH-52}" rx="8"'
                f' fill="#f8fafc" stroke="#e2e8f0" stroke-width="1.5"/>'
                f'<g transform="translate({MX-36},{iy}) scale(3)" fill="none"'
                f' stroke="{c}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">'
                + HAND +
                '</g>'
                f'<text x="{MX}" y="{FH-30}" text-anchor="middle" {FONT}'
                f' font-size="11" fill="#64748b">{sub}</text>'
                '</svg>'
            )

    @api.depends('biodata_ids', 'biophoto_ids')
    def _compute_counts(self):
        Attlog   = self.env['zkteco.device.attlog']
        Biodata  = self.env['zkteco.device.biodata']
        Biophoto = self.env['zkteco.device.biophoto']
        for r in self:
            r.pending_attlog_count = Attlog.search_count([
                ('device_id', '=', r.device_id.id),
                ('pin', '=', r.pin),
                ('state', '=', 'pending'),
            ])
            r.biodata_count  = Biodata.search_count([('device_user_id', '=', r.id)])
            r.biophoto_count = Biophoto.search_count([('device_user_id', '=', r.id)])

    # ── suggestions de mapping par similarité de nom ──────────────

    def get_name_suggestions(self, limit=5):
        """Retourne [(score_pct, hr.employee)] triés par similarité de nom décroissante."""
        self.ensure_one()
        name = (self.name_on_device or '').lower().strip()
        if not name:
            return []
        employees = self.env['hr.employee'].sudo().search([('active', '=', True)])
        scored = sorted(
            [(SequenceMatcher(None, name, (e.name or '').lower().strip()).ratio(), e)
             for e in employees],
            key=lambda x: -x[0],
        )
        return [(round(s * 100), e) for s, e in scored[:limit] if s >= 0.25]

    # ── actions UI ────────────────────────────────────────────────

    def action_view_biodata(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Templates biométriques — {self.name_on_device or self.pin}',
            'res_model': 'zkteco.device.biodata',
            'view_mode': 'list,form',
            'domain': [('device_user_id', '=', self.id)],
            'context': {'default_device_user_id': self.id},
        }

    def action_view_biophoto(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Photos d\'enrôlement — {self.name_on_device or self.pin}',
            'res_model': 'zkteco.device.biophoto',
            'view_mode': 'list,form',
            'domain': [('device_user_id', '=', self.id)],
            'context': {'default_device_user_id': self.id},
        }

    def action_open_mapping_wizard(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': f'Mapper — {self.name_on_device or self.pin}',
            'res_model': 'zkteco.user.mapping.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_device_user_id': self.id},
        }

    def action_delete_all_fingerprints(self):
        self.ensure_one()
        fps = self.biodata_ids.filtered(lambda b: b.bio_type == '1')
        for fp in fps:
            self.device_id._send_command(f'DELETE_FP PIN={self.pin} FID={fp.finger_id}')
        count = len(fps)
        fps.unlink()
        return self._notif(
            'Empreintes supprimées',
            f'{count} empreinte(s) supprimée(s) du device.',
            'warning',
        )

    def action_delete_face(self):
        self.ensure_one()
        faces = self.biodata_ids.filtered(lambda b: b.bio_type in ('9', '2'))
        for face in faces:
            self.device_id._send_command(f'DELETE_BIODATA PIN={self.pin} TYPE={face.bio_type} NO={face.finger_id}')
        faces.unlink()
        return self._notif('Visage supprimé', 'Template(s) visage supprimé(s) du device.', 'warning')

    def action_delete_palm(self):
        self.ensure_one()
        palms = self.biodata_ids.filtered(lambda b: b.bio_type == '8')
        for palm in palms:
            self.device_id._send_command(f'DELETE_BIODATA PIN={self.pin} TYPE=8 NO={palm.finger_id}')
        palms.unlink()
        return self._notif('Paume supprimée', 'Template(s) paume supprimé(s) du device.', 'warning')

    def action_refresh_biodata(self):
        """Demande au device de renvoyer la biométrie réellement stockée pour ce user.
        Le device POST sa BIODATA en retour → _process_biodata → le miroir se met à jour."""
        self.ensure_one()
        self.device_id._send_command(f'QUERY_BIODATA PIN={self.pin}')
        return self._notif(
            'Rafraîchissement demandé',
            "Le device va renvoyer la biométrie stockée. Le panneau se mettra à "
            "jour dans quelques secondes (rechargez la fiche).",
        )

    @staticmethod
    def _notif(title: str, message: str, ntype: str = 'success') -> dict:
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': title, 'message': message, 'type': ntype, 'sticky': False},
        }

    def action_ignore(self):
        self.write({'state': 'ignored'})

    def action_reset_to_new(self):
        self.write({'state': 'new', 'employee_id': False})

    def action_delete_from_device(self):
        for r in self:
            r.device_id._send_command(f'DELETE_USER PIN={r.pin}')
            r.write({'state': 'deleted'})

    # ── mapping core ──────────────────────────────────────────────

    def _do_map(self, employee):
        """
        Lie ce device user à un hr.employee (modèle BioTime : Odoo = maître).
        - si l'employé n'a pas de PIN canonique → adopte celui du device
        - si le device connaît la personne sous un PIN LOCAL différent du canonique
          → CONVERGENCE : re-enrôle sous le PIN canonique + supprime le PIN local
        - propage le lien aux autres device.user 'new' au même PIN local
        - importe tous les zkteco.device.attlog pending de ce PIN local
        """
        self.ensure_one()
        self.write({'employee_id': employee.id, 'state': 'mapped'})

        local_pin = self.pin
        if not employee.zkteco_pin:
            employee.sudo().write({'zkteco_pin': local_pin})
        canonical = employee.zkteco_pin

        # Propagation cross-device : autres sas 'new' au même PIN local
        siblings = self.search([
            ('pin', '=', local_pin),
            ('id', '!=', self.id),
            ('state', '=', 'new'),
        ])
        if siblings:
            siblings.write({'employee_id': employee.id, 'state': 'mapped'})
            _logger.info(
                f"[zkteco] PIN={local_pin} propagé sur {len(siblings)} autre(s) device(s)"
            )

        # Import des pointages en quarantaine (sur le PIN local d'origine)
        pending = self.env['zkteco.device.attlog'].search([
            ('pin', '=', local_pin),
            ('state', '=', 'pending'),
        ])
        if pending:
            pending._import_to_attendance(employee)
            _logger.info(
                f"[zkteco] {len(pending)} pointage(s) importé(s) pour PIN={local_pin} "
                f"→ {employee.name}"
            )

        # Convergence : le device connaît la personne sous un PIN ≠ canonique
        if local_pin != canonical and self.device_id:
            self._converge_to_canonical(employee, local_pin, canonical)

    def _converge_to_canonical(self, employee, local_pin, canonical):
        """Aligne le device de ce sas sur le PIN canonique de l'employé.

        Séquence (modèle BioTime, un seul empcode partout) :
        1. ENROLL_USER canonique + re-sync biométrique best-effort (_sync_to_device)
        2. DELETE_USER de l'ancien PIN local divergent
        3. le sas reflète désormais le PIN canonique (sauf collision device/pin)

        ⚠️ La biométrie est re-poussée depuis Odoo si l'algo est compatible
        (empreintes/visage/paume stockés). Sinon elle devra être ré-enrôlée.
        """
        self.ensure_one()
        device = self.device_id
        employee._sync_to_device(device)                     # ENROLL_USER canonique + bio
        device._send_command(f'DELETE_USER PIN={local_pin}')  # purge l'ancien PIN local
        _logger.warning(
            f"[zkteco] convergence PIN sur {device.serial_number} : "
            f"local {local_pin} → canonique {canonical} ({employee.name})"
        )
        # Le sas reflète le PIN canonique, sauf si un record (device, canonique) existe déjà
        clash = self.search([
            ('device_id', '=', device.id),
            ('pin', '=', canonical),
            ('id', '!=', self.id),
        ], limit=1)
        if not clash:
            self.write({'pin': canonical})

    # ── upsert depuis NATS ────────────────────────────────────────

    @api.model
    def _upsert(self, serial_number: str, pin: str, name: str,
                privilege: int, card: str, employee=None):
        """
        Crée ou met à jour un enregistrement device.user.
        Ne modifie jamais state ni employee_id si déjà posés par l'admin.

        - `employee` fourni → enrôlement TOP-DOWN (depuis Odoo) : on connaît déjà
          l'employé, lien posé immédiatement.
        - `employee` absent → push BOTTOM-UP (device → Odoo) : convergence par PIN
          (modèle « Odoo = maître, PIN canonique » : user PIN=x = employé zkteco_pin=x).
        """
        device = self.env['zkteco.device'].sudo().search(
            [('serial_number', '=', serial_number)], limit=1)
        if not device:
            return

        existing = self.sudo().search(
            [('device_id', '=', device.id), ('pin', '=', pin)], limit=1)

        if employee is None:
            employee = self.env['hr.employee'].sudo().search(
                [('zkteco_pin', '=', pin)], limit=1)

        vals = {
            'name_on_device': name or existing.name_on_device or '',
            'privilege': privilege,
            'card': card or '',
        }
        if existing:
            if employee and not existing.employee_id:
                vals['employee_id'] = employee.id
                vals['state'] = 'mapped'
            existing.write(vals)
        else:
            if employee:
                vals['employee_id'] = employee.id
                vals['state'] = 'mapped'
            self.sudo().create({
                'device_id': device.id,
                'pin': pin,
                'state': vals.pop('state', 'new'),
                **vals,
            })
            _logger.info(
                f"[zkteco] sas: nouveau user PIN={pin} ({name}) sur {serial_number}"
                + (f" → mappé sur {employee.name}" if employee else " (non mappé)"))