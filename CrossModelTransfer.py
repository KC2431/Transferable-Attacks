from art.estimators.classification.scikitlearn import ScikitlearnClassifier, ScikitlearnDecisionTreeClassifier
from art.estimators.classification import XGBoostClassifier
from art.attacks.evasion import HopSkipJump
from art.attacks.evasion import DecisionTreeAttack

from sklearn.metrics import accuracy_score
from sklearn.model_selection import RandomizedSearchCV
from sklearn.svm import SVC
from xgboost import XGBClassifier
from sklearn.naive_bayes import GaussianNB
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier
from sklearn.neighbors import KNeighborsClassifier
from scipy.stats import randint

import numpy as np
import torch
from torch.utils.data import DataLoader

from models import DNN
from CustomDataset import CustomDataset

import warnings
warnings.filterwarnings('ignore')


def CrossModelTransfer(trainingFeatures, 
                       trainingLabels,
                       testFeatures,
                       testLabels, 
                       NNAttackMethod='SAIF'):

    print("================================================================================================================")
    print(f"Conducting Cross Model Transferability.")
    print("================================================================================================================")    

    modelTypeList = ['NN', 'LR', 'GNB','XGB','DT','KNN','SVM']
    modelDict = {}

    hyperparameters = {
        'LR' : {
            'solver' : ['newton-cg', 'lbfgs', 'saga'],
            'C' : [0.001, 0.01, 0.1, 1, 10, 100, 1000]
        },
        'GNB' : {
            'var_smoothing': np.logspace(0,-9, num=100)
        },
        'SVM' : {
            'C': [0.1, 1, 10, 100, 1000],
            'gamma': [1, 0.1, 0.01, 0.001, 0.0001]
        },
        'XGB' : {
            'n_estimators': [50, 100, 300, 500, 1000],
            'learning_rate' : [0.001, 0.01, 0.1, 0.2, 0.3],
            'max_depth': [1, 3, 5, 7, 10, 15]
        },
        'DT' : {
            "max_depth": [3, None],
            "max_features": randint(1, 8),
            "min_samples_leaf": randint(1, 9),
            "criterion": ["gini", "entropy"]
        },
        'KNN' : {
            'n_neighbors': [3, 5, 7, 9, 11],
            'weights': ['uniform', 'distance'],
            'algorithm': ['auto', 'ball_tree', 'kd_tree', 'brute'],
            'p': [1, 2]  # For Minkowski distance
        }
    }

    pipelines = {
        'LR' : LogisticRegression(), 
        'GNB' : GaussianNB(),
        'SVM' : SVC(kernel='rbf'), 
        'XGB' : XGBClassifier(n_jobs=4),
        'DT' : DecisionTreeClassifier(),
        'KNN' : KNeighborsClassifier()
    }

    print(f"Training the models now.")
    print("================================================================================================================")

    for modelName in modelTypeList:
        if modelName == 'NN':
            trainingFeaturesTensor = torch.tensor(trainingFeatures, dtype=torch.float32)
            trainingLabelsTensor= torch.tensor(trainingLabels, dtype=torch.float32)

            data = CustomDataset(X=trainingFeaturesTensor, Y=trainingLabelsTensor)
            trainDataLoader = DataLoader(dataset=data, batch_size=10, shuffle=True)
            
            model = DNN(input_shape=trainingFeatures.shape[1], output_shape=1, attackMethod=NNAttackMethod)
            model.selfTrain(dataloader=trainDataLoader)

        else:
            model = RandomizedSearchCV(pipelines[modelName], 
                                           hyperparameters[modelName],
                                           cv=5,
                                           n_jobs=2,
                                           random_state=42)
            model.fit(trainingFeatures, trainingLabels)

        modelDict[modelName] = model
        print(f"{modelName} trained")
    
    print(f"Testing the models now on the test set.")
    print("================================================================================================================")
    # Measuring the accuracies of the models on test set

    for modelName in modelDict.keys():
        model = modelDict[modelName]

        if modelName != 'NN':
            pred = model.predict(testFeatures)
            accuracy = accuracy_score(testLabels, pred)
            
            print(f"Accuracy for {modelName} on test set is {accuracy * 100:.2f}%")

        else:
            testFeaturesTensor = torch.tensor(testFeatures, dtype=torch.float32)
            testLabelsTensor = torch.tensor(testLabels, dtype=torch.float32)
            
            with torch.no_grad():
                pred = model(testFeaturesTensor)
                
                numCorrect = (pred.squeeze(1).round() == testLabelsTensor).sum()
                numSamples = testFeaturesTensor.shape[0]

            print(f"Accuracy for {modelName} on test set is {numCorrect/numSamples * 100:.2f}%")
    
    print("================================================================================================================")
    print('Attacking the models now with each other.')

    for modelName in modelDict.keys():
        if modelName != 'NN':
            model = modelDict[modelName]

            if modelName not in ['XGB','DT']:
                model = ScikitlearnClassifier(model=model)
            
            elif modelName == 'XGB':
                model = XGBoostClassifier(model=model.best_estimator_, nb_features=testFeatures.shape[1], nb_classes=2, clip_values=(0,1))

            elif modelName == 'DT':
                model = ScikitlearnDecisionTreeClassifier(model=model.best_estimator_)

            if modelName != 'DT':
                attackMethod = HopSkipJump(classifier=model, targeted=False, max_iter=10, max_eval=5000, verbose=True)
            
            elif modelName == 'DT':
                attackMethod = DecisionTreeAttack(classifier=model)

            advTestFeatures = attackMethod.generate(x=testFeatures)
        
        else:
            model = modelDict[modelName]
            model.eval()

            advTestFeatures = model.selfAttack(X=testFeaturesTensor,
                                               Y=testLabelsTensor)

        for evalModelName in modelDict.keys():
            if evalModelName != 'NN':
                evalModel = modelDict[evalModelName]

                if modelName == 'NN': # If previous model is NN, we want numpy arrays
                    pred = evalModel.predict(advTestFeatures.numpy())
                else:
                    pred = evalModel.predict(advTestFeatures)   
                
                transferPercent = 1 - accuracy_score(testLabels, pred)
                print(f"Percentage of transferability to {evalModelName} for adversarial inputs created for {modelName} is {transferPercent*100:.2f}%")

            else:
                evalModel = modelDict[evalModelName]
                evalModel.eval()

                if modelName == 'NN':
                    pred = evalModel(advTestFeatures)
                else:
                    pred = evalModel(torch.tensor(advTestFeatures, dtype=torch.float32))

                numCorrect = (pred.round().squeeze(1) == testLabelsTensor).sum()
                numSamples = testFeatures.shape[0]

                print(f"Percentage of transferability to {evalModelName} for adversarial inputs created for {modelName} is {(1 - numCorrect/numSamples)*100:.2f}%")
        print("================================================================================================================")


if __name__ == '__main__':
    import pandas as pd
    from sklearn.preprocessing import MinMaxScaler
    from sklearn.model_selection import train_test_split

    data = pd.read_csv('Data/diabetes.csv')
    
    X = data.iloc[:,:-1].values
    Y = data.iloc[:,-1].values

    scaler = MinMaxScaler()

    XScaled = scaler.fit_transform(X)
    X_train, X_test, y_train, y_test = train_test_split(XScaled, Y, test_size=0.2, random_state=42)

    CrossModelTransfer(X_train, y_train, X_test, y_test,NNAttackMethod='L1_MAD')